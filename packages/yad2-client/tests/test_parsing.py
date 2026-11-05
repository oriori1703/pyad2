from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from yad2_client.errors import ParseError
from yad2_client.models import Link, PageSnapshot
from yad2_client.parsing import number, parse_categories, parse_item, parse_listing, parse_search


def test_market_fields_and_pagination(snapshot: PageSnapshot) -> None:
    result = parse_search(snapshot)
    listing = result.listings[0]
    assert (listing.title, listing.price, listing.city_id) == ("NVIDIA 5070 ti", 3500, "5000")
    assert listing.condition == "משומש"
    assert listing.attributes == {"manufacture": "Nvidia"}
    assert result.next_url == "https://www.yad2.co.il/market/search?q=RTX&pageNumber=2"
    assert (result.total, result.total_pages, result.page) == (22, 2, 1)


def test_item_identity_not_recommendations(snapshot: PageSnapshot, market_item: dict[str, JsonValue]) -> None:
    item = snapshot.model_copy(
        update={
            "url": "https://www.yad2.co.il/market/item/15405193887804",
            "data": {"props": {"pageProps": {"item": market_item}}},
        }
    )
    assert parse_item(item).listing.id == "15405193887804"
    with pytest.raises(ParseError, match="not present"):
        parse_item(item.model_copy(update={"url": "https://www.yad2.co.il/market/item/123"}))


@pytest.mark.parametrize("value", [None, True, "call for price", "NaN", "Infinity", {}, []])
def test_non_numeric_prices_remain_unknown(value: JsonValue) -> None:
    assert number(value) is None


def test_zero_is_a_price(market_item: dict[str, JsonValue]) -> None:
    assert parse_listing({**market_item, "price": 0}, "market").price == 0
    assert number("₪1,200") == 1200


def test_empty_feed_is_distinct_from_schema_drift(snapshot: PageSnapshot) -> None:
    def feed(data: dict[str, JsonValue]) -> PageSnapshot:
        return snapshot.model_copy(update={"data": {"props": {"pageProps": {"initialFeed": data}}}})

    empty = parse_search(feed({"items": [], "totalItems": 0, "totalPages": 0}))
    assert empty.listings == () and empty.total == 0 and empty.next_url is None
    with pytest.raises(ParseError):
        parse_search(feed({"unknownNewField": []}))
    with pytest.raises(ParseError, match="None of"):
        parse_search(feed({"items": [{"id": 123}]}))


def test_deduplicates_feed_and_reports_partial_failures(
    snapshot: PageSnapshot, market_item: dict[str, JsonValue]
) -> None:
    data: dict[str, JsonValue] = {
        "props": {"pageProps": {"initialFeed": {"items": [market_item, market_item, {"unexpected": "schema"}]}}}
    }
    result = parse_search(snapshot.model_copy(update={"data": data}))
    assert len(result.listings) == 1
    assert len(result.warnings) == 1


@pytest.mark.parametrize("section,group", [("realestate", "yad2"), ("vehicles", "ads")])
def test_other_sections_use_query_feed_not_recommendations(section: str, group: str) -> None:
    row: dict[str, JsonValue] = {
        "token": "abc123",
        "price": 1200,
        "manufacturer": {"text": "Example"},
        "model": {"text": "Car"},
        "address": {"city": {"id": 5000, "text": "תל אביב יפו"}},
        "additionalDetails": {"property": {"text": "דירה"}, "roomsCount": 3},
        "metaData": {"images": ["https://example.com/photo.jpg"]},
    }
    page = PageSnapshot(
        url=f"https://www.yad2.co.il/{section}/search?page=2",
        fetched_at=datetime.now(UTC),
        data={
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {"queryKey": ["similar-links"], "state": {"data": {"ads": [{"id": "wrong"}]}}},
                            {
                                "queryKey": ["feed-mix"],
                                "state": {"data": {group: [row], "pagination": {"total": 60, "totalPages": 3}}},
                            },
                        ]
                    }
                }
            }
        },
    )
    result = parse_search(page)
    assert result.page == 2 and result.total == 60
    assert len(result.listings) == 1
    assert result.listings[0].url == f"https://www.yad2.co.il/{section}/item/abc123"


def test_categories_use_live_slugs_and_unicode_filter(snapshot: PageSnapshot) -> None:
    page = snapshot.model_copy(
        update={
            "links": (
                Link(title="ריהוט", url="https://www.yad2.co.il/market/collections/furniture"),
                Link(title="ריהוט", url="https://www.yad2.co.il/market/collections/furniture"),
                Link(title="Other", url="https://www.yad2.co.il/market"),
            )
        }
    )
    assert len(parse_categories(page, "FURNITURE").categories) == 1
    assert len(parse_categories(page, "ריהוט").categories) == 1
    assert not parse_categories(page, "computer").categories


def test_vehicle_item_query_key(snapshot: PageSnapshot) -> None:
    page = snapshot.model_copy(
        update={
            "url": "https://www.yad2.co.il/vehicles/item/car123",
            "data": {
                "props": {
                    "pageProps": {
                        "dehydratedState": {
                            "queries": [
                                {
                                    "queryKey": ["vehicles", "item", "car123"],
                                    "state": {
                                        "data": {
                                            "token": "car123",
                                            "manufacturer": {"text": "Example"},
                                            "model": {"text": "Car"},
                                        }
                                    },
                                }
                            ]
                        }
                    }
                }
            },
        }
    )
    assert parse_item(page).listing.title == "Example · Car"
