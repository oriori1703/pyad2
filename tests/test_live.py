import pytest

from yad2_client import MarketSearch, Settings, Yad2Client
from yad2_client.shared_browser import session_status


@pytest.mark.live
async def test_live_example_listing_and_market_pagination() -> None:
    async with Yad2Client() as client:
        result = await client.get_listing("15405193887804", refresh=True)
        assert result.listing.id == "15405193887804"
        assert result.listing.title and result.listing.images
        search = await client.search(MarketSearch(query="RTX", page=2), refresh=True)
        assert search.page == 2 and search.listings
        categories = await client.categories("computer", refresh=True)
        assert any(category.slug.endswith("_computer-components") for category in categories.categories)


@pytest.mark.live
async def test_live_http_filters_and_collection_pagination_without_chromium() -> None:
    settings = Settings.from_env()
    if settings.transport != "http":
        pytest.skip("HTTP-first transport is required")
    async with Yad2Client(settings) as client:
        search = MarketSearch(collection="furniture", max_price=1500)
        filters = await client.filters(search, refresh=True)
        assert filters.product_types and filters.attributes
        batch = await client.search_pages(search, max_pages=2, refresh=True)
        assert [page.page for page in batch.pages] == [1, 2]
        assert batch.listings and not batch.warnings
    status = await session_status(settings)
    assert status.service is not None and not status.service.browser_running
