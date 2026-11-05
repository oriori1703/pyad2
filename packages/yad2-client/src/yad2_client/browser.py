"""Reusable Patchright browser with persisted first-party session state."""

import math
import sys
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import anyio
from filelock import FileLock, Timeout
from patchright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from patchright.async_api import Error as PlaywrightError
from patchright.async_api import TimeoutError as PlaywrightTimeout
from pydantic import JsonValue, TypeAdapter, ValidationError

from yad2_client.errors import BrowserError, ChallengeRequired, PageUnavailable, ParseError, RateLimited
from yad2_client.extraction import extract_api, extract_listing, extract_search
from yad2_client.http_state import HttpIdentity
from yad2_client.models import Link, PageSnapshot, RawPayload
from yad2_client.session import browser_lock_status, new_owner, write_private
from yad2_client.settings import Settings
from yad2_client.urls import validate_url

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_LINKS = TypeAdapter(tuple[Link, ...])
_CHALLENGE_MARKERS = ("radware bot manager", "shieldsquare", "are you for real", "verifying your browser")


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class BrowserTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._profile_lock: FileLock | None = None
        self.retry_after = 0.0
        self._user_agent: str | None = None

    @property
    def state_path(self) -> Path:
        return self.settings.data_dir / "browser-state.json"

    @property
    def running(self) -> bool:
        return self._browser is not None

    async def _start(self) -> Page:
        if self._page is not None:
            return self._page
        await anyio.Path(self.settings.data_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        self._profile_lock = FileLock(self.settings.data_dir / "browser.lock")
        try:
            self._profile_lock.acquire(timeout=0)
        except Timeout as exc:
            self._profile_lock = None
            status = await anyio.to_thread.run_sync(browser_lock_status, self.settings)
            owner = f" by PID {status.owner.pid}" if status.owner else " by another process"
            raise BrowserError(
                f"The Yad2 browser session is in use{owner}. Run `yad2 status` for details. "
                "Restart older CLI/MCP processes to enable shared browser access, or finish the active catalog capture."
            ) from exc
        try:
            await write_private(self.settings.data_dir / "browser-owner.json", new_owner("browser"))
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.settings.headless, channel=self.settings.channel
            )
            # Match the installed browser version rather than freezing a stale fingerprint.
            platform = (
                "Macintosh; Intel Mac OS X 10_15_7"
                if sys.platform == "darwin"
                else "Windows NT 10.0; Win64; x64"
                if sys.platform == "win32"
                else "X11; Linux x86_64"
            )
            user_agent = (
                f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{self._browser.version} Safari/537.36"
            )
            self._user_agent = user_agent
            self._context = await self._browser.new_context(
                locale="he-IL",
                user_agent=user_agent,
                storage_state=self.state_path if await anyio.Path(self.state_path).exists() else None,
            )
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self.settings.timeout_seconds * 1000)
            return self._page
        except BaseException:
            await self.close()
            raise

    async def _save_state(self) -> None:
        if self._context is not None:
            # Write to a private temporary path and atomically replace the previous session.
            temporary = self.state_path.with_suffix(".tmp")
            await self._context.storage_state(path=temporary)
            await anyio.Path(temporary).chmod(0o600)
            await anyio.Path(temporary).replace(self.state_path)
            if self._user_agent is not None:
                await write_private(
                    self.settings.data_dir / "http-identity.json", HttpIdentity(user_agent=self._user_agent)
                )

    async def _snapshot(self, page: Page) -> PageSnapshot:
        url = validate_url(page.url)
        data = _JSON_OBJECT.validate_json(await page.locator("#__NEXT_DATA__").inner_text())
        links = _LINKS.validate_python(
            await page.locator('a[href*="/market/collections/"]').evaluate_all(
                "els => els.map(e => ({title: e.textContent.trim(), url: e.href}))"
            )
        )
        await self._save_state()
        return PageSnapshot(url=url, fetched_at=datetime.now(UTC), data=data, links=links)

    async def fetch(self, url: str) -> PageSnapshot:
        url = validate_url(url)
        self.retry_after = 0
        try:
            page = await self._start()
            response = await page.goto(url, wait_until="domcontentloaded")
            validate_url(page.url)
            if response is not None:
                if response.status == 429:
                    self.retry_after = retry_after_seconds(response.headers.get("retry-after"))
                    raise RateLimited("Yad2 returned HTTP 429. Requests paused; try again after the cooldown.")
                if response.status in {401, 403}:
                    raise ChallengeRequired(
                        "Yad2 requires verification. Run `yad2 session` to complete it in a browser."
                    )
                if response.status >= 400:
                    raise PageUnavailable(f"Yad2 returned HTTP {response.status} for {url}")
            try:
                await page.wait_for_selector("#__NEXT_DATA__", state="attached")
            except PlaywrightTimeout as exc:
                body = (await page.title()) + " " + (await page.locator("body").inner_text())[:8000]
                if any(marker in body.casefold() for marker in _CHALLENGE_MARKERS):
                    raise ChallengeRequired("Browser verification required. Run `yad2 session`, then retry.") from exc
                raise ParseError(
                    "Page did not expose Next.js data before the timeout; it may use a different frontend"
                ) from exc
            if urlsplit(url).path.rstrip("/") == "/market":
                # The category navigation is client-rendered; SSR contains only a short footer menu.
                await page.locator('a[href*="/market/collections/"][href$="_computer-components"]').first.wait_for(
                    state="attached"
                )
            return await self._snapshot(page)
        except PlaywrightError as exc:
            raise BrowserError(f"Browser navigation failed: {exc}. Install with `yad2 install-browser`.") from exc

    async def interactive(self, url: str, wait_seconds: float) -> PageSnapshot:
        """Let the user complete a challenge in a visible browser; save only a verified page."""
        try:
            page = await self._start()
            await page.goto(validate_url(url), wait_until="domcontentloaded")
            await page.wait_for_selector("#__NEXT_DATA__", state="attached", timeout=wait_seconds * 1000)
            return await self._snapshot(page)
        except PlaywrightError as exc:
            raise BrowserError(f"Interactive verification did not complete: {exc}") from exc

    async def fetch_search(self, url: str) -> RawPayload:
        return extract_search(await self.fetch(url))

    async def fetch_listing(self, url: str) -> RawPayload:
        return extract_listing(await self.fetch(url))

    async def fetch_filters(self, url: str) -> RawPayload:
        """Capture the live filters requested by the search frontend during one navigation."""
        url = validate_url(url)
        parsed = urlsplit(url)
        collection = parsed.path.startswith("/market/collections/")
        if parsed.path != "/market/search" and not collection:
            raise ValueError("Live filters require a market search or collection URL")
        endpoint = "/recommerce-feed/filters"

        def filters_query(value: str) -> dict[str, list[str]]:
            # The observed frontend forwards these filters, but omits pageNumber.
            keys = {"q", "areas", "cities", "conditions", "minPrice", "maxPrice", "productTypes"}
            return {
                key: values
                for key, values in parse_qs(urlsplit(value).query).items()
                if key in keys or key.startswith("attr_")
            }

        try:
            page = await self._start()
            async with page.expect_response(
                lambda response: (
                    urlsplit(response.url).hostname == "gw.yad2.co.il"
                    and (
                        (
                            urlsplit(response.url).path.startswith(endpoint + "/")
                            or (bool(parse_qs(parsed.query).get("q")) and urlsplit(response.url).path == endpoint)
                        )
                        if collection
                        else urlsplit(response.url).path == endpoint
                    )
                    and filters_query(response.url) == filters_query(url)
                ),
                timeout=self.settings.timeout_seconds * 1000,
            ) as pending:
                snapshot = await self.fetch(url)
            response = await pending.value
            if response.status == 429:
                self.retry_after = retry_after_seconds(response.headers.get("retry-after"))
                raise RateLimited("Live filters returned HTTP 429; requests paused.")
            body = await response.text()
            if response.status in {401, 403} or any(marker in body.casefold() for marker in _CHALLENGE_MARKERS):
                raise ChallengeRequired("Live filters require browser verification. Run `yad2 session`.")
            if response.status >= 400:
                raise PageUnavailable(f"Live filters returned HTTP {response.status}")
            actual_path = urlsplit(snapshot.url).path
            redirected_text_search = (
                collection and bool(parse_qs(parsed.query).get("q")) and actual_path == "/market/search"
            )
            if (actual_path != parsed.path and not redirected_text_search) or filters_query(
                snapshot.url
            ) != filters_query(url):
                raise ParseError("Live filters redirected to a different search")
            if collection and not redirected_text_search:
                props = snapshot.data.get("props")
                page_props = props.get("pageProps") if isinstance(props, dict) else None
                handle = page_props.get("collectionHebHandle") if isinstance(page_props, dict) else None
                if not isinstance(handle, str) or unquote(urlsplit(response.url).path) != f"{endpoint}/{handle}":
                    raise ParseError("Live filter collection does not match the page's collection metadata")
            elif urlsplit(response.url).path != endpoint:
                raise ParseError("Live filter endpoint does not match the search page")
            return extract_api(
                PageSnapshot(url=snapshot.url, fetched_at=datetime.now(UTC), data=_JSON_OBJECT.validate_json(body)),
                "filters",
            )
        except (PlaywrightTimeout, ValidationError) as exc:
            raise ParseError("The search frontend did not return recognized live filter data") from exc
        except PlaywrightError as exc:
            raise BrowserError(f"Live filter capture failed: {exc}") from exc

    async def close(self) -> None:
        with anyio.CancelScope(shield=True):
            try:
                if self._browser is not None:
                    await self._browser.close()
            finally:
                try:
                    if self._playwright is not None:
                        await self._playwright.stop()
                finally:
                    if self._profile_lock is not None:
                        try:
                            await anyio.Path(self.settings.data_dir / "browser-owner.json").unlink(missing_ok=True)
                        finally:
                            self._profile_lock.release()
                    self._page = self._context = self._browser = self._playwright = self._profile_lock = None
