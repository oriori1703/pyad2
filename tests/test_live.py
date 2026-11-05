import pytest

from yad2_client import MarketSearch, Yad2Client


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
