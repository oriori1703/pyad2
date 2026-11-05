import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import httpx2
import pytest
from pydantic import JsonValue

from yad2_client import DataSource, MarketSearch, RawPayload, Settings, Yad2Client
from yad2_client.browser import BrowserTransport
from yad2_client.errors import ParseError
from yad2_client.extraction import extract_search
from yad2_client.filters import parse_filters
from yad2_client.http import HttpTransport
from yad2_client.models import PageSnapshot
from yad2_client.parsing import parse_item, parse_search
from yad2_client.urls import market_url


async def test_search_sources_return_identical_unwrapped_json_and_parse_identically(
    http_settings: Settings, snapshot: PageSnapshot
) -> None:
    upstream = extract_search(snapshot).data
    upstream["unknownUpstreamField"] = {"preserve": [1, "raw"]}
    snapshot = snapshot.model_copy(update={"data": {"props": {"pageProps": {"initialFeed": upstream}}}})
    http = HttpTransport(
        http_settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"data": upstream}))
    )
    browser = BrowserTransport(http_settings)
    assert isinstance(http, DataSource) and isinstance(browser, DataSource)
    with patch.object(browser, "fetch", AsyncMock(return_value=snapshot)):
        browser_payload = await browser.fetch_search(snapshot.url)
    http_payload = await http.fetch_search(snapshot.url)
    assert browser_payload.kind == http_payload.kind == "search"
    assert browser_payload.data == http_payload.data == upstream
    assert "props" not in http_payload.data
    assert browser_payload.url == http_payload.url == snapshot.url
    browser_result, http_result = parse_search(browser_payload), parse_search(http_payload)
    assert browser_result.model_dump(exclude={"fetched_at"}) == http_result.model_dump(exclude={"fetched_at"})
    assert http_payload.data["unknownUpstreamField"] == {"preserve": [1, "raw"]}


@pytest.mark.parametrize("valid", [True, False])
async def test_listing_sources_return_same_object_and_leave_validation_to_shared_parser(
    http_settings: Settings, snapshot: PageSnapshot, market_item: dict[str, JsonValue], valid: bool
) -> None:
    item = market_item if valid else {"id": market_item["id"]}
    snapshot = snapshot.model_copy(
        update={
            "url": f"https://www.yad2.co.il/market/item/{market_item['id']}",
            "data": {"props": {"pageProps": {"item": item, "recommendations": [{"id": 9}]}}},
        }
    )
    http = HttpTransport(
        http_settings,
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, text='<script id="__NEXT_DATA__">' + json.dumps(snapshot.data) + "</script>")
        ),
    )
    browser = BrowserTransport(http_settings)
    with patch.object(browser, "fetch", AsyncMock(return_value=snapshot)):
        browser_payload = await browser.fetch_listing(snapshot.url)
    http_payload = await http.fetch_listing(snapshot.url)
    assert browser_payload.kind == http_payload.kind == "listing"
    assert browser_payload.data == http_payload.data == item
    if valid:
        assert parse_item(browser_payload).listing == parse_item(http_payload).listing
    else:
        for payload in (browser_payload, http_payload):
            with pytest.raises(ParseError, match="title"):
                parse_item(payload)


@pytest.mark.parametrize("valid", [True, False])
async def test_filter_sources_remove_same_envelope_before_shared_validation(
    http_settings: Settings, snapshot: PageSnapshot, valid: bool
) -> None:
    data: dict[str, JsonValue] = (
        {
            "productTypes": [],
            "conditions": [],
            "cities": [],
            "attributes": [],
            "price": {"min": None, "max": None},
            "unknown": "preserved",
        }
        if valid
        else {"unknown": "preserved"}
    )
    body = json.dumps({"data": data})
    response = Mock(status=200, url="https://gw.yad2.co.il/recommerce-feed/filters?q=RTX", headers={})
    response.text = AsyncMock(return_value=body)

    async def response_value() -> Mock:
        return response

    @asynccontextmanager
    async def pending() -> AsyncIterator[Mock]:
        yield Mock(value=response_value())

    page = Mock()
    page.expect_response.return_value = pending()
    browser = BrowserTransport(http_settings)
    with (
        patch.object(browser, "_start", AsyncMock(return_value=page)),
        patch.object(browser, "fetch", AsyncMock(return_value=snapshot)),
    ):
        browser_payload = await browser.fetch_filters(snapshot.url)
    http = HttpTransport(http_settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, text=body)))
    http_payload = await http.fetch_filters(snapshot.url)
    assert browser_payload.kind == http_payload.kind == "filters"
    assert browser_payload.data == http_payload.data == data
    if valid:
        assert parse_filters(browser_payload).model_dump(exclude={"fetched_at"}) == parse_filters(
            http_payload
        ).model_dump(exclude={"fetched_at"})
    else:
        for payload in (browser_payload, http_payload):
            with pytest.raises(ParseError):
                parse_filters(payload)


async def test_client_validates_raw_source_data_before_caching(http_settings: Settings) -> None:
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            200,
            json={"data": {"unknown": "not an empty feed"}},
            headers={"set-cookie": "verified=rotated; Domain=.yad2.co.il; Path=/; Secure"},
        )

    http = HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
    async with Yad2Client(http_settings, transport=http) as client:
        raw = await http.fetch_search(market_url(MarketSearch(query="RTX")))
        assert raw.data == {"unknown": "not an empty feed"}
        for _ in range(2):
            with pytest.raises(ParseError):
                await client.search(MarketSearch(query="RTX"))
        assert (await client.cache.status()).cached_pages == 0
    # Transport success updates cookies independently from downstream domain validation.
    assert "rotated" in (http_settings.data_dir / "browser-state.json").read_text()
    assert calls == 3


async def test_parsed_payload_cache_is_shared_across_source_implementations(
    http_settings: Settings, snapshot: PageSnapshot
) -> None:
    search = MarketSearch(query="RTX")
    browser = BrowserTransport(http_settings)
    with patch.object(browser, "fetch", AsyncMock(return_value=snapshot)):
        async with Yad2Client(http_settings, transport=browser) as client:
            first = await client.search(search)

    def unexpected(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Switching sources must reuse the valid raw-payload cache")

    http = HttpTransport(http_settings, transport=httpx2.MockTransport(unexpected))
    async with Yad2Client(http_settings, transport=http) as client:
        second = await client.search(search)
        assert second.cached and second.listings == first.listings and second.fetched_at == first.fetched_at
        assert await client.cache.get(market_url(search), 300) is None


def test_shared_parsers_reject_wrong_payload_kinds(snapshot: PageSnapshot) -> None:
    payload = RawPayload(kind="listing", url=snapshot.url, fetched_at=snapshot.fetched_at, data={})
    with pytest.raises(ParseError, match="search payload"):
        parse_search(payload)
    with pytest.raises(ParseError, match="filters payload"):
        parse_filters(payload)
    with pytest.raises(ParseError, match="listing payload"):
        parse_item(payload.model_copy(update={"kind": "search"}))
