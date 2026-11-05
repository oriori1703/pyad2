"""Client for the automatically started, per-data-directory browser service."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from functools import partial

import anyio
from filelock import FileLock, Timeout

from yad2_client.cache import Cache
from yad2_client.errors import BrowserError, ChallengeRequired, PageUnavailable, ParseError, RateLimited, Yad2Error
from yad2_client.models import PageSnapshot
from yad2_client.session import (
    PROTOCOL_VERSION,
    ServiceRecord,
    ServiceRequest,
    ServiceResponse,
    SessionStatus,
    browser_lock_status,
    read_record,
    receive_response,
)
from yad2_client.settings import Settings
from yad2_client.urls import validate_url

_CHILDREN: list[subprocess.Popen[bytes]] = []
_CONNECTION_ERRORS = (
    OSError,
    TimeoutError,
    anyio.EndOfStream,
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    anyio.IncompleteRead,
    anyio.DelimiterNotFound,
)


def _normalized(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"data_dir": settings.data_dir.resolve(), "cache_dir": settings.cache_dir.resolve()}
    )


async def _exchange(record: ServiceRecord, request: ServiceRequest) -> ServiceResponse:
    async with await anyio.connect_tcp("127.0.0.1", record.port) as stream:
        await stream.send(request.model_dump_json().encode() + b"\n")
        return await receive_response(stream)


def _check_response(response: ServiceResponse) -> ServiceResponse:
    if response.error is not None:
        errors: dict[str, type[Yad2Error]] = {
            error.code: error for error in (BrowserError, ChallengeRequired, RateLimited, PageUnavailable, ParseError)
        }
        raise errors.get(response.error.code, BrowserError)(response.error.message)
    return response


async def _existing(settings: Settings) -> ServiceRecord | None:
    record = await read_record(settings)
    if record is None:
        return None
    try:
        with anyio.fail_after(2):
            response = await _exchange(record, ServiceRequest(token=record.token, operation="status"))
    except _CONNECTION_ERRORS:
        return None
    _check_response(response)
    if record.protocol != PROTOCOL_VERSION or record.settings != settings:
        raise BrowserError("Browser service settings/version differ. Run `yad2 browser-stop`, then retry your command.")
    return record


def _spawn(settings: Settings) -> subprocess.Popen[bytes]:
    _CHILDREN[:] = [child for child in _CHILDREN if child.poll() is None]
    environment = dict(os.environ)
    environment.update({f"YAD2_{key.upper()}": str(value) for key, value in settings.model_dump().items()})
    log_path = settings.data_dir / "browser-service.log"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "yad2_client.browser_service"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            env=environment,
            start_new_session=True,
        )
    _CHILDREN.append(process)
    return process


async def ensure_service(settings: Settings) -> ServiceRecord:
    settings = _normalized(settings)
    if (record := await _existing(settings)) is not None:
        return record
    await anyio.Path(settings.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = FileLock(settings.data_dir / "service-start.lock", thread_local=False)
    try:
        await anyio.to_thread.run_sync(partial(guard.acquire, timeout=settings.service_start_timeout_seconds))
    except Timeout as exc:
        raise BrowserError("Timed out waiting for browser service startup. Run `yad2 status`.") from exc
    try:
        if (record := await _existing(settings)) is not None:
            return record
        process = await anyio.to_thread.run_sync(_spawn, settings)
        try:
            with anyio.fail_after(settings.service_start_timeout_seconds):
                while True:
                    if process.poll() is not None:
                        raise BrowserError(
                            f"Browser service could not start. Run `yad2 status`; see {settings.data_dir / 'browser-service.log'}."
                        )
                    record = await read_record(settings)
                    if record is not None and record.pid == process.pid:
                        return record
                    # Bounded local readiness wait; no browser request is retried.
                    await anyio.sleep(0.05)
        except TimeoutError as exc:
            raise BrowserError("Browser service startup timed out. Run `yad2 status`.") from exc
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(guard.release)


class SharedBrowserTransport:
    """Each RPC uses a short-lived connection; closing a client leaves the shared browser available."""

    def __init__(self, settings: Settings) -> None:
        self.settings = _normalized(settings)

    async def _request(self, operation: str, url: str, wait_seconds: float = 180) -> PageSnapshot:
        url = validate_url(url)
        record = await ensure_service(self.settings)
        request = ServiceRequest.model_validate(
            {"token": record.token, "operation": operation, "url": url, "wait_seconds": wait_seconds}
        )
        timeout = self.settings.browser_queue_timeout_seconds + 3 * self.settings.timeout_seconds + 10
        if operation == "verify":
            timeout += wait_seconds
        try:
            with anyio.fail_after(timeout):
                response = _check_response(await _exchange(record, request))
        except _CONNECTION_ERRORS as exc:
            raise BrowserError(
                "Lost contact with the browser service. The request was not replayed. Run `yad2 status`."
            ) from exc
        if response.snapshot is None:
            raise BrowserError("Browser service returned no page snapshot")
        return response.snapshot

    async def fetch(self, url: str) -> PageSnapshot:
        return await self._request("fetch", url)

    async def interactive(self, url: str, wait_seconds: float) -> PageSnapshot:
        return await self._request("verify", url, wait_seconds)

    async def close(self) -> None:
        pass


async def stop_service(settings: Settings) -> bool:
    record = await read_record(settings)
    if record is None:
        return False
    try:
        with anyio.fail_after(5):
            _check_response(await _exchange(record, ServiceRequest(token=record.token, operation="stop")))
    except _CONNECTION_ERRORS as exc:
        raise BrowserError("Browser service is unreachable. Run `yad2 status` to inspect the owner.") from exc
    try:
        await anyio.to_thread.run_sync(_wait_stopped, settings)
    except Timeout as exc:
        raise BrowserError("Browser service is still shutting down. Run `yad2 status`.") from exc
    return True


def _wait_stopped(settings: Settings) -> None:
    with FileLock(settings.data_dir / "service.lock", timeout=5):
        pass


async def session_status(settings: Settings) -> SessionStatus:
    settings = _normalized(settings)
    record = await read_record(settings)
    state = "stopped"
    activity = None
    guidance: list[str] = []
    if record is not None:
        state = "unreachable"
        try:
            with anyio.fail_after(2):
                response = _check_response(
                    await _exchange(record, ServiceRequest(token=record.token, operation="status"))
                )
            activity = response.activity
            state = "running"
        except (*_CONNECTION_ERRORS, BrowserError):
            guidance.append("Service metadata exists but the service is unreachable; the next request can restart it.")
    lock = await anyio.to_thread.run_sync(browser_lock_status, settings)
    if lock.locked and (activity is None or lock.owner is None or lock.owner.pid != activity.pid):
        guidance.append(
            "A direct or older browser process owns the session. Finish its work or restart its MCP server."
        )
    path = anyio.Path(settings.data_dir / "browser-state.json")
    try:
        modified = datetime.fromtimestamp((await path.stat()).st_mtime, UTC)
    except FileNotFoundError:
        modified = None
    cache = await Cache(settings.cache_dir).status()
    if cache.error:
        guidance.append("The cache could not be read; inspect cache.error before trying another browser request.")
    if cache.cooldown_remaining_seconds:
        guidance.append(
            "Requests are cooling down; cached reads remain available. Use `yad2 session` for verification."
        )
    return SessionStatus.model_validate(
        {
            "checked_at": datetime.now(UTC),
            "data_dir": settings.data_dir,
            "cache_dir": settings.cache_dir,
            "service_state": state,
            "last_known_service_pid": record.pid if record is not None else None,
            "service": activity,
            "browser_lock": lock,
            "saved_state_exists": modified is not None,
            "saved_state_updated_at": modified,
            "cache": cache,
            "guidance": tuple(guidance),
        }
    )
