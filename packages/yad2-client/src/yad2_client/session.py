"""Private local service protocol and credential-free session diagnostics."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio
from anyio.abc import ByteStream
from anyio.streams.buffered import BufferedByteReceiveStream
from filelock import FileLock, Timeout
from pydantic import Field

from yad2_client.models import Model, PageSnapshot
from yad2_client.settings import Settings

PROTOCOL_VERSION = 2
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class SessionOwner(Model):
    pid: int
    started_at: datetime
    purpose: str


class ServiceRecord(Model):
    protocol: int = PROTOCOL_VERSION
    pid: int
    started_at: datetime
    port: int = Field(ge=1, le=65535)
    token: str = Field(min_length=64, max_length=64)
    settings: Settings


class BrowserActivity(Model):
    pid: int
    started_at: datetime
    browser_running: bool
    operation: Literal["fetch", "filters", "verify"] | None = None
    queued_requests: int = 0


class ServiceRequest(Model):
    protocol: int = PROTOCOL_VERSION
    token: str
    operation: Literal["fetch", "filters", "verify", "status", "stop"]
    url: str = ""
    wait_seconds: float = Field(default=180, ge=1, le=600)


class ServiceError(Model):
    code: str
    message: str


class ServiceResponse(Model):
    snapshot: PageSnapshot | None = None
    activity: BrowserActivity | None = None
    error: ServiceError | None = None


class BrowserLockStatus(Model):
    locked: bool
    owner: SessionOwner | None = None


class CacheStatus(Model):
    cached_pages: int | None = 0
    cooldown_remaining_seconds: float | None = 0
    next_request_in_seconds: float | None = 0
    error: str | None = None


class SessionStatus(Model):
    checked_at: datetime
    data_dir: Path
    cache_dir: Path
    service_state: Literal["stopped", "running", "unreachable"]
    last_known_service_pid: int | None = None
    service: BrowserActivity | None = None
    browser_lock: BrowserLockStatus
    saved_state_exists: bool
    saved_state_updated_at: datetime | None = None
    cache: CacheStatus
    guidance: tuple[str, ...] = ()


async def write_private(path: Path, model: Model) -> None:
    """Create private metadata before writing, then atomically publish it."""
    await anyio.to_thread.run_sync(_write_private, path, model.model_dump_json())


def _write_private(path: Path, content: str) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def read_record(settings: Settings) -> ServiceRecord | None:
    try:
        return ServiceRecord.model_validate_json(
            await anyio.Path(settings.data_dir / "browser-service.json").read_text("utf-8")
        )
    except (FileNotFoundError, ValueError):
        return None


async def send_response(stream: ByteStream, response: ServiceResponse) -> None:
    await stream.send(response.model_dump_json().encode() + b"\n")


async def receive_response(stream: ByteStream) -> ServiceResponse:
    body = await BufferedByteReceiveStream(stream).receive_until(b"\n", MAX_RESPONSE_BYTES)
    return ServiceResponse.model_validate_json(body)


def browser_lock_status(settings: Settings) -> BrowserLockStatus:
    path = settings.data_dir / "browser.lock"
    if not path.exists():
        return BrowserLockStatus(locked=False)
    try:
        with FileLock(path, timeout=0):
            return BrowserLockStatus(locked=False)
    except Timeout:
        try:
            owner = SessionOwner.model_validate_json((settings.data_dir / "browser-owner.json").read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            owner = None
        return BrowserLockStatus(locked=True, owner=owner)


def new_owner(purpose: str) -> SessionOwner:
    return SessionOwner(pid=os.getpid(), started_at=datetime.now(UTC), purpose=purpose)
