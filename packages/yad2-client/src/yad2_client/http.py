"""Direct, paced HTTP reads using an explicitly verified browser session."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit

import anyio
import httpx2
from filelock import FileLock, Timeout
from pydantic import JsonValue, TypeAdapter, ValidationError

from yad2_client.browser import retry_after_seconds
from yad2_client.cache import Cache
from yad2_client.errors import BrowserError, ChallengeRequired, PageUnavailable, ParseError, RateLimited
from yad2_client.extraction import extract_api, extract_listing, page_props
from yad2_client.http_state import BrowserState, HttpIdentity
from yad2_client.models import PageSnapshot, RawPayload
from yad2_client.session import new_owner, write_private
from yad2_client.settings import Settings
from yad2_client.urls import ORIGIN, validate_url

_JSON = TypeAdapter(dict[str, JsonValue])
_GATEWAY = "https://gw.yad2.co.il/recommerce-feed"
_FILTER_KEYS = {"q", "sortOption", "cities", "areas", "conditions", "productTypes", "minPrice", "maxPrice"}
_CHALLENGES = ("radware bot manager", "shieldsquare", "are you for real", "verifying your browser")


class _NextData(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True
            self.scripts += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.active = False

    def handle_data(self, data: str) -> None:
        if self.active:
            self.parts.append(data)


def html_snapshot(url: str, body: str) -> PageSnapshot:
    parser = _NextData()
    parser.feed(body)
    if parser.scripts != 1:
        if any(marker in body.casefold() for marker in _CHALLENGES):
            raise ChallengeRequired("HTTP session requires browser verification. Run `yad2 session`.")
        raise ParseError("HTTP page did not expose one Next.js payload")
    try:
        data = _JSON.validate_json("".join(parser.parts))
    except ValidationError as exc:
        raise ParseError("Malformed Next.js payload in HTTP response") from exc
    return PageSnapshot(url=url, fetched_at=datetime.now(UTC), data=data)


def supports_search(url: str) -> bool:
    parsed = urlsplit(validate_url(url))
    path_supported = parsed.path == "/market/search" or bool(
        re.fullmatch(r"/market/collections/[a-z0-9_-]+", parsed.path)
    )
    # Observed collection + q URLs redirect to the global search frontend.
    mixed_query = parsed.path.startswith("/market/collections/") and bool(parse_qs(parsed.query).get("q"))
    return (
        path_supported
        and not mixed_query
        and all(
            key in _FILTER_KEYS or key == "pageNumber" or re.fullmatch(r"attr_\d+", key)
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        )
    )


class HttpTransport:
    """Owns pacing and the session lock. Use through Yad2Client for persistent challenge cooldown."""

    def __init__(self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.cache = Cache(settings.cache_dir)
        self.transport = transport
        self.retry_after = 0.0
        self._collections: dict[str, str] = {}

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[tuple[httpx2.AsyncClient, HttpIdentity]]:
        await anyio.Path(self.settings.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        guard = FileLock(self.settings.data_dir / "browser.lock", timeout=0)
        try:
            guard.acquire()
        except Timeout as exc:
            raise BrowserError("The shared session is in use. Run `yad2 status` to inspect its owner.") from exc
        try:
            await write_private(self.settings.data_dir / "browser-owner.json", new_owner("HTTP session"))
            try:
                identity = HttpIdentity.model_validate_json(
                    await anyio.Path(self.settings.data_dir / "http-identity.json").read_text()
                )
                state = BrowserState.model_validate_json(
                    await anyio.Path(self.settings.data_dir / "browser-state.json").read_text()
                )
            except (FileNotFoundError, ValidationError) as exc:
                raise ChallengeRequired("Initialize or repair the HTTP session with `yad2 session`.") from exc
            self.retry_after = 0
            async with httpx2.AsyncClient(
                cookies=state.cookie_jar(),
                transport=self.transport,
                timeout=self.settings.timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": identity.user_agent, "Accept-Language": "he-IL,he;q=0.9,en;q=0.8"},
            ) as client:
                yield client, identity
                await write_private(self.settings.data_dir / "browser-state.json", state.with_cookies(client.cookies))
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await anyio.Path(self.settings.data_dir / "browser-owner.json").unlink(missing_ok=True)
                finally:
                    guard.release()

    async def _get(
        self,
        client: httpx2.AsyncClient,
        url: str,
        *,
        referer: str,
        params: dict[str, str] | None = None,
        json_response: bool = False,
    ) -> httpx2.Response:
        await self.cache.wait_turn(self.settings.min_interval_seconds)
        try:
            response = await client.get(
                url,
                params=params,
                headers={
                    "Referer": referer,
                    "Origin": ORIGIN,
                    "Accept": "application/json, text/plain, */*"
                    if json_response
                    else "text/html,application/xhtml+xml",
                },
            )
        except httpx2.HTTPError as exc:
            raise BrowserError(f"HTTP request failed ({type(exc).__name__}); it was not replayed") from exc
        if response.status_code == 429:
            self.retry_after = retry_after_seconds(response.headers.get("retry-after"))
            raise RateLimited("Yad2 returned HTTP 429; requests paused.")
        if response.status_code in {401, 403}:
            raise ChallengeRequired("HTTP session requires verification. Run `yad2 session`.")
        if any(marker in response.text[:8000].casefold() for marker in _CHALLENGES) and (
            json_response or "__NEXT_DATA__" not in response.text
        ):
            raise ChallengeRequired("HTTP session requires verification. Run `yad2 session`.")
        if 300 <= response.status_code < 400:
            raise PageUnavailable("Yad2 redirected the HTTP request; the redirect was not followed")
        if response.status_code >= 400:
            raise PageUnavailable(f"Yad2 returned HTTP {response.status_code}")
        return response

    async def _collection(self, client: httpx2.AsyncClient, url: str) -> str:
        path = urlsplit(url).path
        if path == "/market/search":
            return ""
        if path not in self._collections:
            response = await self._get(client, ORIGIN + path, referer=url)
            snapshot = html_snapshot(ORIGIN + path, response.text)
            props = page_props(snapshot)
            handle = props.get("collectionHebHandle")
            if (
                not isinstance(handle, str)
                or not handle
                or props.get("collectionEnglishHandle") != path.rsplit("/", 1)[-1]
            ):
                raise ParseError("Collection metadata does not identify the requested collection")
            self._collections[path] = handle
        return "/" + quote(self._collections[path], safe="")

    async def fetch(self, url: str) -> PageSnapshot:
        url = validate_url(url)
        async with self._session() as (client, _):
            response = await self._get(client, url, referer=ORIGIN + "/market")
            return html_snapshot(url, response.text)

    async def _feed(self, url: str, *, filters: bool) -> RawPayload:
        url = validate_url(url)
        if not supports_search(url):
            raise ValueError("HTTP search supports verified marketplace filters only")
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("Repeated query keys are not supported; use comma-separated filter values")
        params = dict(pairs)
        page = params.pop("pageNumber", "1")
        if not page.isdecimal() or not 1 <= int(page) <= 10000:
            raise ValueError("Page number must be between 1 and 10000")
        params["itemsPerPage"] = "10" if filters else "21"
        async with self._session() as (client, identity):
            suffix = await self._collection(client, url)
            if not filters:
                params["pageNumber"] = page
                params["scrollSessionId"] = identity.scroll_session_id.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                )
            response = await self._get(
                client,
                f"{_GATEWAY}/{'filters' if filters else 'search'}{suffix}",
                referer=url,
                params=params,
                json_response=True,
            )
            try:
                payload = _JSON.validate_json(response.text)
            except ValidationError as exc:
                raise ParseError("HTTP API did not return a JSON object") from exc
            return extract_api(
                PageSnapshot(url=url, fetched_at=datetime.now(UTC), data=payload), "filters" if filters else "search"
            )

    async def fetch_search(self, url: str) -> RawPayload:
        return await self._feed(url, filters=False)

    async def fetch_filters(self, url: str) -> RawPayload:
        return await self._feed(url, filters=True)

    async def fetch_listing(self, url: str) -> RawPayload:
        return extract_listing(await self.fetch(url))

    async def close(self) -> None:
        # Connections are scoped to each operation; no browser or socket survives it.
        pass
