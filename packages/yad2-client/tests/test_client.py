import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yad2_client import MarketSearch, Settings, Yad2Client
from yad2_client.cache import Cache
from yad2_client.errors import ChallengeRequired, ParseError, RateLimited
from yad2_client.models import PageSnapshot


class FakeTransport:
    def __init__(self, snapshot: PageSnapshot, error: Exception | None = None) -> None:
        self.snapshot = snapshot
        self.error = error
        self.calls = 0
        self.closed = False

    async def fetch(self, url: str) -> PageSnapshot:
        self.calls += 1
        if self.error:
            raise self.error
        return self.snapshot.model_copy(update={"url": url})

    async def close(self) -> None:
        self.closed = True


async def test_concurrent_identical_calls_fetch_once(settings: Settings, snapshot: PageSnapshot) -> None:
    transport = FakeTransport(snapshot)
    async with Yad2Client(settings, transport=transport) as client:
        first, second = await asyncio.gather(
            client.search(MarketSearch(query="RTX")), client.search(MarketSearch(query="RTX"))
        )
        assert not first.cached and second.cached
        assert transport.calls == 1
        fresh = await client.search(MarketSearch(query="RTX"), refresh=True)
        assert not fresh.cached and transport.calls == 2
    assert transport.closed


async def test_cache_persists_between_clients(settings: Settings, snapshot: PageSnapshot) -> None:
    async with Yad2Client(settings, transport=FakeTransport(snapshot)) as client:
        first = await client.search(MarketSearch(query="GPU"))
    transport = FakeTransport(snapshot, error=AssertionError("Cache hit must not start a browser"))
    async with Yad2Client(settings, transport=transport) as client:
        second = await client.search(MarketSearch(query="GPU"))
    assert second.cached and first.fetched_at == second.fetched_at
    assert transport.calls == 0


async def test_challenge_opens_persistent_circuit_and_cache_stays_available(
    settings: Settings, snapshot: PageSnapshot
) -> None:
    transport = FakeTransport(snapshot)
    async with Yad2Client(settings, transport=transport) as client:
        await client.search(MarketSearch(query="GPU"))
        transport.error = ChallengeRequired("Verify browser")
        with pytest.raises(ChallengeRequired):
            await client.search(MarketSearch(query="chair"))
        assert transport.closed
        assert (await client.search(MarketSearch(query="GPU"))).cached
    async with Yad2Client(settings, transport=transport) as client:
        with pytest.raises(RateLimited):
            await client.search(MarketSearch(query="chair"), refresh=True)
        await client.cache.clear()
        with pytest.raises(RateLimited):
            await client.search(MarketSearch(query="GPU"))
    assert transport.calls == 2


async def test_schema_failures_are_not_cached(settings: Settings, snapshot: PageSnapshot) -> None:
    transport = FakeTransport(snapshot.model_copy(update={"data": {}}))
    async with Yad2Client(settings, transport=transport) as client:
        with pytest.raises(ParseError):
            await client.search(MarketSearch())
        transport.snapshot = snapshot
        assert not (await client.search(MarketSearch())).cached
    assert transport.calls == 2


async def test_invalid_url_never_reaches_transport(settings: Settings, snapshot: PageSnapshot) -> None:
    transport = FakeTransport(snapshot)
    async with Yad2Client(settings, transport=transport) as client:
        with pytest.raises(ValueError):
            await client.search_url("https://localhost/admin")
    assert transport.calls == 0


async def test_pacing_is_shared_between_cache_instances(tmp_path: Path) -> None:
    clock = AsyncMock()
    with patch("yad2_client.cache.time.time", return_value=1000), patch("yad2_client.cache.asyncio.sleep", clock):
        await Cache(tmp_path).wait_turn(3)
        await Cache(tmp_path).wait_turn(3)
    clock.assert_awaited_once_with(3)


async def test_ttl_expiration(settings: Settings, snapshot: PageSnapshot) -> None:
    cache = Cache(settings.cache_dir)
    with patch("yad2_client.cache.time.time", return_value=1000):
        await cache.put(snapshot.url, snapshot)
    with patch("yad2_client.cache.time.time", return_value=1301):
        assert await cache.get(snapshot.url, 300) is None
