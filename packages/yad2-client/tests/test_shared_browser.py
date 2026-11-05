import os
import sys

import anyio
import pytest
from anyio.lowlevel import checkpoint
from filelock import FileLock

from yad2_client import Settings
from yad2_client.browser_service import serve_browser
from yad2_client.cache import Cache
from yad2_client.errors import BrowserError, ChallengeRequired, RateLimited
from yad2_client.models import PageSnapshot
from yad2_client.session import ServiceRequest, new_owner, read_record, write_private
from yad2_client.shared_browser import SharedBrowserTransport, _exchange, session_status, stop_service


class FakeBrowser:
    retry_after = 0.0

    def __init__(self, snapshot: PageSnapshot) -> None:
        self.snapshot = snapshot
        self.running = False
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.closed = 0
        self.error: Exception | None = None
        self.entered = anyio.Event()
        self.release = anyio.Event()
        self.release.set()

    async def fetch(self, url: str) -> PageSnapshot:
        self.running = True
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.entered.set()
            await self.release.wait()
            await checkpoint()
            if self.error is not None:
                raise self.error
            return self.snapshot.model_copy(update={"url": url})
        finally:
            self.active -= 1

    async def interactive(self, url: str, wait_seconds: float) -> PageSnapshot:
        return await self.fetch(url)

    async def close(self) -> None:
        self.closed += 1
        self.running = False


async def test_clients_share_serial_navigation_and_closing_a_client_keeps_service_alive(
    settings: Settings, snapshot: PageSnapshot
) -> None:
    browser = FakeBrowser(snapshot)
    browser.release = anyio.Event()
    results: list[PageSnapshot] = []

    async def fetch() -> None:
        transport = SharedBrowserTransport(settings)
        results.append(await transport.fetch(snapshot.url))
        await transport.close()

    with anyio.fail_after(10):
        async with anyio.create_task_group() as server_tasks:
            record = await server_tasks.start(serve_browser, settings, lambda _: browser)
            async with anyio.create_task_group() as clients:
                clients.start_soon(fetch)
                clients.start_soon(fetch)
                await browser.entered.wait()
                status = await session_status(settings)
                assert status.service is not None and status.service.operation == "fetch"
                with pytest.raises(BrowserError, match="busy"):
                    await stop_service(settings)
                browser.release.set()
            assert len(results) == 2
            assert browser.calls == 2 and browser.max_active == 1
            status = await session_status(settings)
            assert status.service is not None and status.service.browser_running
            assert status.service.pid == record.pid
            assert "token" not in status.model_dump_json()
            assert await stop_service(settings)
    assert browser.closed == 1
    assert await read_record(settings) is None


async def test_challenge_blocks_other_clients_and_verification_reopens_visible_browser(
    settings: Settings, snapshot: PageSnapshot
) -> None:
    browsers: list[FakeBrowser] = []
    modes: list[bool] = []

    def factory(configuration: Settings) -> FakeBrowser:
        browser = FakeBrowser(snapshot)
        if not browsers:
            browser.error = ChallengeRequired("Verify browser")
        browsers.append(browser)
        modes.append(configuration.headless)
        return browser

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            await tasks.start(serve_browser, settings, factory)
            first = SharedBrowserTransport(settings)
            second = SharedBrowserTransport(settings)
            with pytest.raises(ChallengeRequired):
                await first.fetch(snapshot.url)
            with pytest.raises(RateLimited):
                await second.fetch(snapshot.url)
            assert browsers[0].calls == 1 and not browsers[0].running
            status = await session_status(settings)
            assert status.cache.cooldown_remaining_seconds is not None
            assert status.cache.cooldown_remaining_seconds > 0
            await second.interactive(snapshot.url, 5)
            assert modes == [True, False, True]
            assert (await Cache(settings.cache_dir).status()).cooldown_remaining_seconds == 0
            assert (await first.fetch(snapshot.url)).url == snapshot.url
            assert await stop_service(settings)


async def test_status_reports_direct_owner_and_state_metadata_without_credentials(settings: Settings) -> None:
    await anyio.Path(settings.data_dir).mkdir(parents=True)
    owner = new_owner("catalog capture")
    await write_private(settings.data_dir / "browser-owner.json", owner)
    await anyio.Path(settings.data_dir / "browser-state.json").write_text('{"cookies": [{"value": "test-secret"}]}')
    with FileLock(settings.data_dir / "browser.lock", timeout=0):
        status = await session_status(settings)
        assert status.browser_lock.locked and status.browser_lock.owner == owner
        assert status.saved_state_exists and status.saved_state_updated_at is not None
        assert "test-secret" not in status.model_dump_json()
        assert status.guidance
    # Stale metadata cannot make an unlocked session appear occupied.
    assert not (await session_status(settings)).browser_lock.locked


async def test_status_preserves_an_unreadable_cache_and_reports_unknown_budget(settings: Settings) -> None:
    await anyio.Path(settings.cache_dir).mkdir(parents=True)
    path = anyio.Path(settings.cache_dir / "cache.sqlite3")
    await path.write_text("not a database")
    status = await session_status(settings)
    assert status.cache.error
    assert status.cache.cooldown_remaining_seconds is None
    assert await path.read_text() == "not a database"


async def test_local_service_rejects_bad_credentials_and_foreign_urls(
    settings: Settings, snapshot: PageSnapshot
) -> None:
    browser = FakeBrowser(snapshot)
    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            record = await tasks.start(serve_browser, settings, lambda _: browser)
            response = await _exchange(record, ServiceRequest(token="wrong", operation="stop"))
            assert response.error is not None
            response = await _exchange(
                record, ServiceRequest(token=record.token, operation="fetch", url="https://example.com")
            )
            assert response.error is not None
            assert browser.calls == 0
            assert await stop_service(settings)


async def test_service_expires_without_starting_a_browser(settings: Settings, snapshot: PageSnapshot) -> None:
    settings = settings.model_copy(update={"service_idle_seconds": 1})
    browser = FakeBrowser(snapshot)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            await tasks.start(serve_browser, settings, lambda _: browser)
    assert browser.calls == 0 and browser.closed == 1
    assert await read_record(settings) is None


async def test_independent_processes_start_one_service_and_recover_stale_metadata(settings: Settings) -> None:
    environment = dict(os.environ)
    environment.update({f"YAD2_{key.upper()}": str(value) for key, value in settings.model_dump().items()})
    command = [
        sys.executable,
        "-c",
        (
            "import anyio; from yad2_client import Settings; "
            "from yad2_client.shared_browser import ensure_service; "
            "print(anyio.run(ensure_service, Settings.from_env()).pid)"
        ),
    ]
    owners: list[int] = []

    async def start() -> None:
        result = await anyio.run_process(command, env=environment)
        owners.append(int(result.stdout))

    try:
        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(start)
                tasks.start_soon(start)
            assert owners[0] == owners[1] and owners[0] != os.getpid()
            status = await session_status(settings)
            assert status.service is not None and not status.service.browser_running
            record = await read_record(settings)
            assert record is not None
            assert await stop_service(settings)
            assert await read_record(settings) is None
            await write_private(settings.data_dir / "browser-service.json", record)
            assert (await session_status(settings)).service_state == "unreachable"
            await start()
            assert owners[-1] != owners[0]
            assert (await session_status(settings)).service_state == "running"
    finally:
        with anyio.CancelScope(shield=True):
            await stop_service(settings)
            assert await read_record(settings) is None


async def test_queue_timeout_does_not_start_another_navigation(settings: Settings, snapshot: PageSnapshot) -> None:
    settings = settings.model_copy(update={"browser_queue_timeout_seconds": 1})
    browser = FakeBrowser(snapshot)
    browser.release = anyio.Event()
    first = SharedBrowserTransport(settings)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as server_tasks:
            await server_tasks.start(serve_browser, settings, lambda _: browser)
            async with anyio.create_task_group() as clients:
                clients.start_soon(first.fetch, snapshot.url)
                await browser.entered.wait()
                with pytest.raises(BrowserError, match="queue"):
                    await SharedBrowserTransport(settings).fetch(snapshot.url)
                browser.release.set()
            assert browser.calls == 1
            assert await stop_service(settings)
