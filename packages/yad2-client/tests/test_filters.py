import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

import pytest
from pydantic import JsonValue

from yad2_client import MarketSearch, Settings, Yad2Client
from yad2_client.browser import BrowserTransport
from yad2_client.errors import ChallengeRequired, ParseError, RateLimited
from yad2_client.filters import parse_filters
from yad2_client.models import PageSnapshot


def filter_data() -> dict[str, JsonValue]:
    # Minimal observed /recommerce-feed/filters?itemsPerPage=10&q=RTX shape.
    return {
        "data": {
            "productTypes": [{"value": "285", "text": "רכיבי מחשב", "count": 53}],
            "conditions": [{"value": "4", "text": "דרוש תיקון/שיפוץ", "count": 1}],
            "cities": [{"value": "5000", "text": "תל אביב יפו", "count": 23}],
            "price": {"min": 1, "max": 111111},
            "attributes": [
                {
                    "attributeId": 24,
                    "attributeTextHeb": "יצרן",
                    "attributeTextEng": "manufacture",
                    "totalOptionsCount": 225,
                    "options": [{"value": "12538", "text": "Nvidia", "count": 12}],
                }
            ],
        }
    }


def test_live_filters_preserve_counts_and_report_partial_options(snapshot: PageSnapshot) -> None:
    result = parse_filters(snapshot.model_copy(update={"data": filter_data()}))
    assert result.product_types[0].id == "285"
    assert result.conditions[0].id == "4"
    assert result.attributes[0].options[0].count == 12
    assert result.attributes[0].total_options == 225
    assert not result.attributes[0].options_complete


def test_explicit_empty_filters_differ_from_missing_schema(snapshot: PageSnapshot) -> None:
    empty: dict[str, JsonValue] = {
        "data": {
            "productTypes": [],
            "conditions": [],
            "cities": [],
            "attributes": [],
            "price": {"min": None, "max": None},
        }
    }
    assert not parse_filters(snapshot.model_copy(update={"data": empty})).attributes
    with pytest.raises(ParseError):
        parse_filters(snapshot.model_copy(update={"data": {"data": {}}}))


async def test_filter_cache_is_separate_and_bad_shapes_are_not_cached(
    settings: Settings, snapshot: PageSnapshot
) -> None:
    transport = Mock()
    transport.fetch = AsyncMock(return_value=snapshot)
    transport.fetch_filters = AsyncMock(return_value=snapshot.model_copy(update={"data": {"data": {}}}))
    transport.close = AsyncMock()
    async with Yad2Client(settings, transport=transport) as client:
        await client.search(MarketSearch(query="RTX"))
        with pytest.raises(ParseError):
            await client.filters(MarketSearch(query="RTX"))
        transport.fetch_filters.return_value = snapshot.model_copy(update={"data": filter_data()})
        assert not (await client.filters(MarketSearch(query="RTX"))).cached
        assert (await client.filters(MarketSearch(query="RTX"))).cached
        assert (await client.search(MarketSearch(query="RTX"))).cached
        assert transport.fetch_filters.await_count == 2
        assert transport.fetch.await_count == 1


@pytest.mark.parametrize(
    "status,body,error",
    [
        (200, json.dumps(filter_data()), None),
        (200, "<title>Radware Bot Manager Captcha</title>", ChallengeRequired),
        (429, "{}", RateLimited),
        (200, "not JSON", ParseError),
    ],
)
@pytest.mark.parametrize("collection", [False, True])
async def test_capture_observed_frontend_response_in_one_navigation(
    settings: Settings,
    snapshot: PageSnapshot,
    status: int,
    body: str,
    error: type[Exception] | None,
    collection: bool,
) -> None:
    endpoint = "https://gw.yad2.co.il/recommerce-feed/filters"
    if collection:
        endpoint += "/" + quote("ריהוט")
        snapshot = snapshot.model_copy(
            update={
                "url": "https://www.yad2.co.il/market/collections/furniture?q=RTX&productTypes=285",
                "data": {"props": {"pageProps": {"collectionHebHandle": "ריהוט"}}},
            }
        )
    else:
        snapshot = snapshot.model_copy(update={"url": snapshot.url + "&productTypes=285"})
    response = Mock(status=status, headers={"retry-after": "600"}, url=endpoint + "?q=RTX&productTypes=285")
    response.text = AsyncMock(return_value=body)

    async def value() -> Mock:
        return response

    @asynccontextmanager
    async def pending() -> AsyncIterator[Mock]:
        yield Mock(value=value())

    page = Mock()
    page.expect_response.return_value = pending()
    browser = BrowserTransport(settings)
    with (
        patch.object(browser, "_start", AsyncMock(return_value=page)),
        patch.object(browser, "fetch", AsyncMock(return_value=snapshot)) as fetch,
    ):
        if error:
            with pytest.raises(error):
                await browser.fetch_filters(snapshot.url)
        else:
            captured = await browser.fetch_filters(snapshot.url)
            assert parse_filters(captured).attributes[0].id == "24"
        fetch.assert_awaited_once_with(snapshot.url)
        predicate = page.expect_response.call_args.args[0]
        assert predicate(response)
        assert not predicate(Mock(url=endpoint + "?q=sofa&productTypes=285"))
        assert not predicate(Mock(url=endpoint + "?q=RTX"))
        assert not predicate(Mock(url=endpoint + "?q=RTX&productTypes=285&conditions=3"))
        if collection and error is None:
            response.url = (
                "https://gw.yad2.co.il/recommerce-feed/filters/" + quote("מחשבים") + "?q=RTX&productTypes=285"
            )
            page.expect_response.return_value = pending()
            with pytest.raises(ParseError, match="collection"):
                await browser.fetch_filters(snapshot.url)
