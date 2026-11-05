from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from yad2_agent.watches import WatchDefinition, WatchError, WatchStore
from yad2_client import MarketSearch, Settings, Yad2Client
from yad2_client.errors import ChallengeRequired
from yad2_client.models import Listing, SearchBatch, SearchPage


def batch(values: tuple[tuple[str, float | None], ...], *, complete: bool = True) -> SearchBatch:
    return SearchBatch(
        listings=tuple(
            Listing(
                id=identifier,
                url=f"https://www.yad2.co.il/market/item/{identifier}",
                title=identifier,
                section="market",
                price=price,
            )
            for identifier, price in values
        ),
        pages=(
            SearchPage(
                source_url="https://www.yad2.co.il/market/search?q=RTX",
                page=1,
                fetched_at=datetime.now(UTC),
                cached=False,
                total=len(values),
                total_pages=1 if complete else 5,
            ),
        ),
        next_url=None,
        stop_reason="exhausted" if complete else "page_limit",
        complete=complete,
        duplicates_removed=0,
    )


class StubClient(Yad2Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.scan = batch((("1", 100), ("2", 0)))
        self.error: Exception | None = None
        self.before_return: Callable[[], Awaitable[None]] | None = None

    async def search_pages(
        self, search: MarketSearch, *, max_pages: int = 3, max_items: int = 250, refresh: bool = False
    ) -> SearchBatch:
        assert refresh  # Checks cannot compare a cached page as a fresh observation.
        if self.error:
            raise self.error
        if self.before_return:
            await self.before_return()
        return self.scan


async def test_saved_checks_track_prices_new_and_returned_listings_without_false_missing(
    agent_settings: Settings,
) -> None:
    store = WatchStore(agent_settings.data_dir)
    definition = WatchDefinition(name="gpu", search=MarketSearch(query="RTX"))
    await store.save(definition)
    client = StubClient(agent_settings)
    initial = await store.check("gpu", client)
    assert initial.baseline_created and not initial.new_listings
    # Persistence survives a new store/client lifetime.
    store = WatchStore(agent_settings.data_dir)
    client.scan = batch((("1", 80), ("3", 300)), complete=False)
    changes = await store.check("gpu", client)
    assert [listing.id for listing in changes.new_listings] == ["3"]
    assert changes.price_changes[0].is_drop and changes.price_changes[0].old_price == 100
    assert not changes.no_longer_in_results
    client.scan = batch((("1", 80),))
    changes = await store.check("gpu", client)
    assert {listing.id for listing in changes.no_longer_in_results} == {"2", "3"}
    assert all(not listing.present for listing in changes.no_longer_in_results)
    assert not (await store.check("gpu", client)).no_longer_in_results
    client.scan = batch((("1", 80), ("2", 5)))
    changes = await store.check("gpu", client)
    assert [listing.id for listing in changes.returned_listings] == ["2"]
    assert changes.price_changes[0].old_price == 0 and not changes.price_changes[0].is_drop
    assert not changes.new_listings
    assert (await store.save(definition)).last_checked is not None
    changed = definition.model_copy(update={"search": MarketSearch(query="sofa")})
    assert (await store.save(changed)).last_checked is None


async def test_failed_checks_and_concurrent_definition_changes_preserve_state(agent_settings: Settings) -> None:
    store = WatchStore(agent_settings.data_dir)
    definition = WatchDefinition(name="gpu", search=MarketSearch(query="RTX"))
    await store.save(definition)
    client = StubClient(agent_settings)
    await store.check("gpu", client)
    before = await store.list()
    client.error = ChallengeRequired("Verify")
    with pytest.raises(ChallengeRequired):
        await store.check("gpu", client)
    assert await store.list() == before
    client.error = None

    async def replace() -> None:
        await store.delete("gpu")
        await store.save(WatchDefinition(name="gpu", search=MarketSearch(query="sofa")))

    client.before_return = replace
    with pytest.raises(WatchError, match="changed during"):
        await store.check("gpu", client)
    saved = (await store.list()).watches[0]
    assert saved.definition.search.query == "sofa" and saved.last_checked is None


async def test_empty_list_and_explicit_delete(agent_settings: Settings) -> None:
    store = WatchStore(agent_settings.data_dir)
    assert not (await store.list()).watches
    assert not agent_settings.data_dir.exists()
    await store.save(WatchDefinition(name="gpu", search=MarketSearch(query="RTX")))
    assert (await store.delete("gpu")).deleted
    assert not (await store.delete("gpu")).deleted
    with pytest.raises(WatchError, match="does not exist"):
        await store.check("gpu", StubClient(agent_settings))


async def test_overlapping_checks_cannot_overwrite_newer_observations(agent_settings: Settings) -> None:
    store = WatchStore(agent_settings.data_dir)
    await store.save(WatchDefinition(name="gpu", search=MarketSearch(query="RTX")))
    slow, fast = StubClient(agent_settings), StubClient(agent_settings)
    fast.scan = batch((("1", 50),))

    async def finish_newer() -> None:
        await store.check("gpu", fast)

    slow.before_return = finish_newer
    with pytest.raises(WatchError, match="changed during"):
        await store.check("gpu", slow)
    assert not (await store.check("gpu", fast)).price_changes


async def test_unreadable_database_reports_domain_error_and_preserves_data(agent_settings: Settings) -> None:
    store = WatchStore(agent_settings.data_dir)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("not a database")
    with pytest.raises(WatchError, match="database"):
        await store.list()
    assert store.path.read_text() == "not a database"
