"""HTTP-first service backend with explicit browser-only routes and verification."""

from urllib.parse import urlsplit

from yad2_client.browser import BrowserTransport
from yad2_client.cache import Cache
from yad2_client.http import HttpTransport, supports_search
from yad2_client.models import PageSnapshot, RawPayload
from yad2_client.settings import Settings
from yad2_client.urls import validate_url


class HybridTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.browser = BrowserTransport(settings)
        self.http = HttpTransport(settings)
        self.cache = Cache(settings.cache_dir)
        self._using_http = False

    @property
    def running(self) -> bool:
        return self.browser.running

    @property
    def retry_after(self) -> float:
        return self.http.retry_after if self._using_http else self.browser.retry_after

    async def _prepare(self, url: str, *, feed: bool = False) -> bool:
        url = validate_url(url)
        path = urlsplit(url).path
        supported = supports_search(url) if feed else (path.startswith("/market/item/") or supports_search(url))
        self._using_http = self.settings.transport == "http" and supported
        if self._using_http:
            # Release browser ownership and reload the latest cookies for HTTP.
            await self.browser.close()
        else:
            await self.cache.wait_turn(self.settings.min_interval_seconds)
        return self._using_http

    async def fetch(self, url: str) -> PageSnapshot:
        return await self.http.fetch(url) if await self._prepare(url) else await self.browser.fetch(url)

    async def fetch_search(self, url: str) -> RawPayload:
        return (
            await self.http.fetch_search(url)
            if await self._prepare(url, feed=True)
            else await self.browser.fetch_search(url)
        )

    async def fetch_filters(self, url: str) -> RawPayload:
        return (
            await self.http.fetch_filters(url)
            if await self._prepare(url, feed=True)
            else await self.browser.fetch_filters(url)
        )

    async def fetch_listing(self, url: str) -> RawPayload:
        return await self.http.fetch_listing(url) if await self._prepare(url) else await self.browser.fetch_listing(url)

    async def interactive(self, url: str, wait_seconds: float) -> PageSnapshot:
        self._using_http = False
        return await self.browser.interactive(url, wait_seconds)

    async def close(self) -> None:
        await self.browser.close()
        await self.http.close()
