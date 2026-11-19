"""Portable copy of the verified HTTP session, limited to what Yad2 HTTP reads send."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

import anyio
from filelock import FileLock, Timeout
from pydantic import Field, ValidationError, field_validator

from yad2_client.errors import BrowserError, ChallengeRequired
from yad2_client.http_state import BrowserState, HttpIdentity, StoredCookie
from yad2_client.models import Model
from yad2_client.session import new_owner, write_private
from yad2_client.settings import Settings

_SITE = "yad2.co.il"
# Account tokens observed on a signed-in session; public reads work without them.
_ACCOUNT_COOKIES = frozenset({"access_token", "refresh_token"})


def _is_site_cookie(cookie: StoredCookie) -> bool:
    domain = cookie.domain.lstrip(".").lower()
    return domain == _SITE or domain.endswith(f".{_SITE}")


class SessionBundle(Model):
    """A credential: whoever holds it can browse Yad2 as this verified session."""

    version: Literal[1] = 1
    exported_at: datetime
    identity: HttpIdentity
    cookies: tuple[StoredCookie, ...] = Field(min_length=1)

    @field_validator("cookies")
    @classmethod
    def _only_site_cookies(cls, cookies: tuple[StoredCookie, ...]) -> tuple[StoredCookie, ...]:
        if not all(_is_site_cookie(cookie) for cookie in cookies):
            raise ValueError("a session bundle may contain only yad2.co.il cookies")
        return cookies


def parse_bundle(content: str) -> SessionBundle:
    """Validate bundle JSON; errors name the failing fields but never echo cookie values."""
    try:
        return SessionBundle.model_validate_json(content)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'bundle'}: {error['type']}"
            for error in exc.errors(include_input=False, include_context=False, include_url=False)[:5]
        )
        raise ValueError(f"Not a valid Yad2 session bundle ({problems})") from None


@asynccontextmanager
async def _session_lock(settings: Settings, purpose: str) -> AsyncIterator[None]:
    await anyio.Path(settings.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = FileLock(settings.data_dir / "browser.lock", timeout=0)
    try:
        guard.acquire()
    except Timeout as exc:
        raise BrowserError(
            "The shared session is in use. Run `yad2 status` to inspect its owner, or `yad2 browser-stop`."
        ) from exc
    try:
        await write_private(settings.data_dir / "browser-owner.json", new_owner(purpose))
        yield
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await anyio.Path(settings.data_dir / "browser-owner.json").unlink(missing_ok=True)
            finally:
                guard.release()


async def export_session(settings: Settings) -> SessionBundle:
    """Copy the saved Yad2 cookies and HTTP identity; other sites, local storage and account tokens stay behind."""
    async with _session_lock(settings, "session export"):
        try:
            identity = HttpIdentity.model_validate_json(
                await anyio.Path(settings.data_dir / "http-identity.json").read_text()
            )
            state = BrowserState.model_validate_json(
                await anyio.Path(settings.data_dir / "browser-state.json").read_text()
            )
        except (FileNotFoundError, ValidationError) as exc:
            raise ChallengeRequired("No complete saved session to export. Run `yad2 session` first.") from exc
    now = datetime.now(UTC).timestamp()
    cookies = tuple(
        cookie
        for cookie in state.cookies
        if _is_site_cookie(cookie)
        and cookie.name not in _ACCOUNT_COOKIES
        and (cookie.expires < 0 or cookie.expires > now)
    )
    if not cookies:
        raise ChallengeRequired("The saved session has no current Yad2 cookies. Run `yad2 session` first.")
    return SessionBundle(exported_at=datetime.now(UTC), identity=identity, cookies=cookies)


async def import_session(settings: Settings, bundle: SessionBundle, *, replace: bool = False) -> None:
    """Install a bundle as the saved session. An existing session is kept unless `replace` is set."""
    async with _session_lock(settings, "session import"):
        state_path = settings.data_dir / "browser-state.json"
        identity_path = settings.data_dir / "http-identity.json"
        if not replace and (await anyio.Path(state_path).exists() or await anyio.Path(identity_path).exists()):
            raise BrowserError(
                f"A saved session already exists in {settings.data_dir}. Pass `--replace` to overwrite it."
            )
        await write_private(state_path, BrowserState(cookies=list(bundle.cookies)))
        await write_private(identity_path, bundle.identity)
