"""One private loopback service owns and serializes the browser for CLI and MCP clients."""

import hmac
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import anyio
from anyio.abc import ByteStream, SocketAttribute
from anyio.streams.buffered import BufferedByteReceiveStream
from filelock import FileLock, Timeout
from pydantic import TypeAdapter, ValidationError

from yad2_client.browser import BrowserTransport
from yad2_client.cache import Cache
from yad2_client.errors import BrowserError, ChallengeRequired, RateLimited, Yad2Error
from yad2_client.models import PageSnapshot
from yad2_client.session import (
    PROTOCOL_VERSION,
    BrowserActivity,
    ServiceError,
    ServiceRecord,
    ServiceRequest,
    ServiceResponse,
    send_response,
    write_private,
)
from yad2_client.settings import Settings
from yad2_client.urls import validate_url


class BrowserSession(Protocol):
    retry_after: float

    @property
    def running(self) -> bool: ...

    async def fetch(self, url: str) -> PageSnapshot: ...

    async def fetch_filters(self, url: str) -> PageSnapshot: ...

    async def interactive(self, url: str, wait_seconds: float) -> PageSnapshot: ...

    async def close(self) -> None: ...


class BrowserService:
    def __init__(self, settings: Settings, factory: Callable[[Settings], BrowserSession] = BrowserTransport) -> None:
        self.settings = settings
        self.factory = factory
        self.browser = factory(settings)
        self.cache = Cache(settings.cache_dir)
        self.lock = anyio.Lock()
        self.stopped = anyio.Event()
        self.started_at = datetime.now(UTC)
        self.last_activity = anyio.current_time()
        self.operation: str | None = None
        self.queued = 0
        self.token = secrets.token_hex(32)
        self.stopping = False

    def activity(self) -> BrowserActivity:
        return BrowserActivity.model_validate(
            {
                "pid": os.getpid(),
                "started_at": self.started_at,
                "browser_running": self.browser.running,
                "operation": self.operation,
                "queued_requests": self.queued,
            }
        )

    async def dispatch(self, request: ServiceRequest) -> ServiceResponse:
        if not hmac.compare_digest(request.token, self.token):
            raise BrowserError("Invalid browser service credentials")
        if request.protocol != PROTOCOL_VERSION:
            raise BrowserError("Browser service protocol differs; restart it with `yad2 browser-stop`.")
        if request.operation == "status":
            self.last_activity = anyio.current_time()
            return ServiceResponse(activity=self.activity())
        if self.stopping:
            raise BrowserError("Browser service is stopping. Run your command again after it exits.")
        if request.operation == "stop":
            if self.operation is not None or self.queued:
                raise BrowserError(
                    "Browser service is busy. Wait for the current requests to finish before stopping it."
                )
            self.stopping = True
            return ServiceResponse()
        url = validate_url(request.url)
        self.queued += 1
        try:
            with anyio.fail_after(self.settings.browser_queue_timeout_seconds):
                await self.lock.acquire()
        except TimeoutError as exc:
            raise BrowserError(
                "Timed out in the shared browser queue. Run `yad2 status` to see the active operation."
            ) from exc
        finally:
            self.queued -= 1
        try:
            self.operation = request.operation
            if request.operation == "verify":
                await self.browser.close()
                self.browser = self.factory(self.settings.model_copy(update={"headless": False}))
                try:
                    with anyio.fail_after(request.wait_seconds + 2 * self.settings.timeout_seconds):
                        snapshot = await self.browser.interactive(url, request.wait_seconds)
                    await self.cache.reset_cooldown()
                    return ServiceResponse(snapshot=snapshot)
                finally:
                    await self.browser.close()
                    self.browser = self.factory(self.settings)
            # Check the shared cooldown only after reaching the front of the queue.
            await self.cache.wait_turn(self.settings.min_interval_seconds)
            try:
                with anyio.fail_after(3 * self.settings.timeout_seconds):
                    snapshot = (
                        await self.browser.fetch_filters(url)
                        if request.operation == "filters"
                        else await self.browser.fetch(url)
                    )
                return ServiceResponse(snapshot=snapshot)
            except (ChallengeRequired, RateLimited):
                await self.cache.block(max(self.settings.cooldown_seconds, self.browser.retry_after))
                await self.browser.close()
                raise
        except TimeoutError as exc:
            await self.browser.close()
            raise BrowserError("Shared browser operation timed out. Run `yad2 status`.") from exc
        finally:
            self.operation = None
            self.last_activity = anyio.current_time()
            self.lock.release()

    async def handle(self, stream: ByteStream) -> None:
        async with stream:
            try:
                with anyio.fail_after(10):
                    body = await BufferedByteReceiveStream(stream).receive_until(b"\n", 16384)
                request = ServiceRequest.model_validate_json(body)
                try:
                    response = await self.dispatch(request)
                except Yad2Error as exc:
                    response = ServiceResponse(error=ServiceError(code=exc.code, message=str(exc)))
                except ValueError:
                    response = ServiceResponse(
                        error=ServiceError(code="browser_error", message="Invalid service request")
                    )
                except OSError as exc:
                    response = ServiceResponse(
                        error=ServiceError(code="browser_error", message=f"Browser I/O failed: {exc}")
                    )
                try:
                    with anyio.fail_after(10):
                        await send_response(stream, response)
                finally:
                    if request.operation == "stop" and response.error is None:
                        self.stopped.set()
            except (
                OSError,
                TimeoutError,
                ValidationError,
                anyio.EndOfStream,
                anyio.IncompleteRead,
                anyio.DelimiterNotFound,
                anyio.BrokenResourceError,
            ):
                # A disconnected/malformed local client must not stop the shared service or replay navigation.
                return

    async def expire(self) -> None:
        while not self.stopped.is_set():
            await anyio.sleep(min(1, self.settings.service_idle_seconds))
            if (
                self.operation is None
                and not self.queued
                and anyio.current_time() - self.last_activity >= self.settings.service_idle_seconds
            ):
                self.stopping = True
                self.stopped.set()


async def serve_browser(
    settings: Settings,
    factory: Callable[[Settings], BrowserSession] = BrowserTransport,
    *,
    task_status: anyio.abc.TaskStatus[ServiceRecord] = anyio.TASK_STATUS_IGNORED,
) -> None:
    await anyio.Path(settings.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = FileLock(settings.data_dir / "service.lock", timeout=0)
    try:
        guard.acquire()
    except Timeout as exc:
        raise BrowserError("A browser service already owns this data directory. Run `yad2 status`.") from exc
    service = BrowserService(settings, factory)
    path = settings.data_dir / "browser-service.json"
    try:
        async with await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0) as listener:
            address = TypeAdapter(tuple[str, int]).validate_python(listener.extra(SocketAttribute.local_address))
            record = ServiceRecord(
                pid=os.getpid(), started_at=service.started_at, port=address[1], token=service.token, settings=settings
            )
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(listener.serve, service.handle)
                tasks.start_soon(service.expire)
                await write_private(path, record)
                task_status.started(record)
                await service.stopped.wait()
                tasks.cancel_scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await service.browser.close()
            finally:
                try:
                    await anyio.Path(path).unlink(missing_ok=True)
                finally:
                    guard.release()


def main() -> None:
    anyio.run(serve_browser, Settings.from_env(), backend="asyncio")


if __name__ == "__main__":
    main()
