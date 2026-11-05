"""Async client shared by human and agent interfaces."""

import asyncio
from types import TracebackType
from typing import Self

from yad2_client.browser import BrowserTransport, Transport
from yad2_client.cache import Cache
from yad2_client.errors import ChallengeRequired, ParseError, RateLimited
from yad2_client.models import CategoryResult, ListingResult, MarketSearch, PageSnapshot, SearchResult
from yad2_client.parsing import parse_categories, parse_item, parse_search
from yad2_client.settings import Settings
from yad2_client.urls import ORIGIN, listing_url, market_url, validate_url


class Yad2Client:
    def __init__(self, settings: Settings | None = None, *, transport: Transport | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.transport = transport or BrowserTransport(self.settings)
        self.cache = Cache(self.settings.cache_dir)
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self.transport.close()

    async def _load(self, url: str, refresh: bool) -> PageSnapshot:
        if not refresh:
            cached = await self.cache.get(url, self.settings.cache_ttl_seconds)
            if cached is not None:
                return cached
        await self.cache.wait_turn(self.settings.min_interval_seconds)
        try:
            return await self.transport.fetch(url)
        except (ChallengeRequired, RateLimited):
            retry_after = self.transport.retry_after if isinstance(self.transport, BrowserTransport) else 0
            await self.cache.block(max(self.settings.cooldown_seconds, retry_after))
            # Release the session lock so `yad2 session` can repair it while an MCP server stays running.
            await self.transport.close()
            raise

    async def search(self, search: MarketSearch, *, refresh: bool = False) -> SearchResult:
        """Search all market categories, one upstream page at a time."""
        return await self.search_url(market_url(search), refresh=refresh)

    async def search_url(self, url: str, *, refresh: bool = False) -> SearchResult:
        """Read a feed URL, preserving site-specific filters across Yad2 sections."""
        url = validate_url(url)
        async with self._lock:
            snapshot = await self._load(url, refresh)
            result = parse_search(snapshot)
            if not snapshot.cached:
                await self.cache.put(url, snapshot)
            return result

    async def get_listing(self, value: str, *, refresh: bool = False) -> ListingResult:
        url = listing_url(value)
        async with self._lock:
            snapshot = await self._load(url, refresh)
            result = parse_item(snapshot)
            if result.listing.url.rstrip("/") != url.rstrip("/"):
                raise ParseError("Yad2 redirected to a different listing; the requested item may be unavailable")
            if not snapshot.cached:
                await self.cache.put(url, snapshot)
            return result

    async def categories(self, query: str = "", *, refresh: bool = False) -> CategoryResult:
        """Discover collection names/URLs from Yad2's category navigation."""
        url = f"{ORIGIN}/market"
        async with self._lock:
            snapshot = await self._load(url, refresh)
            result = parse_categories(snapshot, query)
            if not snapshot.cached:
                await self.cache.put(url, snapshot)
            return result

    async def page(self, url: str, *, refresh: bool = False) -> PageSnapshot:
        """Raw structured page data for new sections/adapters; no listing-shape assumptions."""
        url = validate_url(url)
        async with self._lock:
            snapshot = await self._load(url, refresh)
            if not snapshot.cached:
                await self.cache.put(url, snapshot)
            return snapshot
