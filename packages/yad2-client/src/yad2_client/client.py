"""Async client shared by human and agent interfaces."""

from types import TracebackType
from typing import Self

import anyio

from yad2_client.browser import BrowserTransport, FilterTransport, Transport
from yad2_client.cache import Cache
from yad2_client.errors import ChallengeRequired, ParseError, RateLimited
from yad2_client.filters import parse_filters
from yad2_client.models import (
    CategoryResult,
    ListingResult,
    MarketSearch,
    PageSnapshot,
    SearchBatch,
    SearchFilters,
    SearchResult,
)
from yad2_client.pagination import collect_pages
from yad2_client.parsing import parse_categories, parse_item, parse_search
from yad2_client.settings import Settings
from yad2_client.shared_browser import SharedBrowserTransport
from yad2_client.urls import ORIGIN, listing_url, market_url, validate_url


class Yad2Client:
    def __init__(self, settings: Settings | None = None, *, transport: Transport | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.transport = transport or SharedBrowserTransport(self.settings)
        self.cache = Cache(self.settings.cache_dir)
        self._lock = anyio.Lock()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.close()

    async def close(self) -> None:
        with anyio.CancelScope(shield=True):
            await self.transport.close()

    async def _load(self, url: str, refresh: bool, *, filters: bool = False) -> PageSnapshot:
        key = f"filters:{url}" if filters else url
        if not refresh:
            cached = await self.cache.get(key, self.settings.cache_ttl_seconds)
            if cached is not None:
                return cached
        shared = isinstance(self.transport, SharedBrowserTransport)
        if not shared:
            await self.cache.wait_turn(self.settings.min_interval_seconds)
        try:
            if filters:
                if not isinstance(self.transport, FilterTransport):
                    raise ParseError("This custom transport does not support live filter discovery")
                return await self.transport.fetch_filters(url)
            return await self.transport.fetch(url)
        except (ChallengeRequired, RateLimited):
            if not shared:
                retry_after = self.transport.retry_after if isinstance(self.transport, BrowserTransport) else 0
                await self.cache.block(max(self.settings.cooldown_seconds, retry_after))
            # Release the session lock so `yad2 session` can repair it while an MCP server stays running.
            await self.transport.close()
            raise

    async def search(self, search: MarketSearch, *, refresh: bool = False) -> SearchResult:
        """Search all market categories, one upstream page at a time."""
        return await self.search_url(market_url(search), refresh=refresh)

    async def filters(self, search: MarketSearch, *, refresh: bool = False) -> SearchFilters:
        """Discover options represented by this search; counts and availability are query-specific."""
        if not search.query.strip() and not search.collection:
            raise ValueError("Live filter discovery requires a query or a collection")
        url = market_url(search)
        async with self._lock:
            snapshot = await self._load(url, refresh, filters=True)
            result = parse_filters(snapshot)
            if not snapshot.cached:
                await self.cache.put(f"filters:{url}", snapshot)
            return result

    async def search_pages(
        self, search: MarketSearch, *, max_pages: int = 3, max_items: int = 250, refresh: bool = False
    ) -> SearchBatch:
        return await self.search_url_pages(
            market_url(search), max_pages=max_pages, max_items=max_items, refresh=refresh
        )

    async def search_url_pages(
        self, url: str, *, max_pages: int = 3, max_items: int = 250, refresh: bool = False
    ) -> SearchBatch:
        """Read pages sequentially, deduplicate by URL, and report coverage conservatively."""
        return await collect_pages(self.search_url, url, max_pages=max_pages, max_items=max_items, refresh=refresh)

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
