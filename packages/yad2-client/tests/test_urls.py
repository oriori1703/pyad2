from urllib.parse import parse_qs, urlsplit

import pytest

from yad2_client import MarketSearch
from yad2_client.urls import listing_url, market_url, validate_url


def test_market_filters_and_unicode_are_encoded() -> None:
    url = market_url(
        MarketSearch(
            query="ספה & כורסא", collection="furniture", min_price=0, max_price=1000, cities=("5000", "3000"), page=2
        )
    )
    assert urlsplit(url).path == "/market/collections/furniture"
    params = parse_qs(urlsplit(url).query)
    assert params["q"] == ["ספה & כורסא"]
    assert params["cities"] == ["5000,3000"]
    assert params["minPrice"] == ["0"] and params["pageNumber"] == ["2"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com",
        "http://www.yad2.co.il",
        "https://www.yad2.co.il.evil.com",
        "https://user@www.yad2.co.il",
        "https://www.yad2.co.il:123",
        "//evil.com",
        "https://www.yad2.co.il\\@evil.com",
        "https://www.yad2.co.il/\nfoo",
    ],
)
def test_rejects_foreign_and_ambiguous_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_url(url)


def test_listing_id_and_url() -> None:
    assert listing_url("123") == "https://www.yad2.co.il/market/item/123"
    assert listing_url("https://yad2.co.il/vehicles/item/abc?tracking=1") == "https://www.yad2.co.il/vehicles/item/abc"
    with pytest.raises(ValueError):
        listing_url("https://www.yad2.co.il/market/search")


def test_bad_filter_ranges() -> None:
    with pytest.raises(ValueError):
        MarketSearch(min_price=20, max_price=10)
    with pytest.raises(ValueError):
        MarketSearch(cities=("Tel Aviv",))
    with pytest.raises(ValueError):
        MarketSearch(collection="../bad")
