import json
from unittest.mock import AsyncMock, patch

import anyio
import httpx2
import pytest
from filelock import FileLock

from yad2_client import AttributeFilter, MarketSearch, Settings, Yad2Client
from yad2_client.browser import BrowserTransport
from yad2_client.browser_service import serve_browser
from yad2_client.cache import Cache
from yad2_client.errors import BrowserError, ChallengeRequired, PageUnavailable, ParseError, RateLimited
from yad2_client.http import HttpTransport, html_snapshot, supports_search
from yad2_client.http_state import BrowserState, StoredCookie
from yad2_client.hybrid import HybridTransport
from yad2_client.models import PageSnapshot
from yad2_client.parsing import parse_search
from yad2_client.shared_browser import SharedBrowserTransport, session_status, stop_service
from yad2_client.urls import market_url


def feed(page: int = 1) -> dict[str, object]:
    return {
        "data": {
            "items": [{"id": page, "title": "Example", "price": 0}],
            "totalItems": 2,
            "totalPages": 2,
            "currentPage": page,
        }
    }


def empty_filters() -> dict[str, object]:
    return {
        "data": {
            "productTypes": [],
            "conditions": [],
            "cities": [],
            "attributes": [],
            "price": {"min": None, "max": None},
        }
    }


async def test_api_pagination_preserves_filters_and_uses_no_browser(http_settings: Settings) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=feed(int(request.url.params["pageNumber"])))

    search = MarketSearch(
        query="RTX",
        product_types=("285",),
        cities=("5000",),
        conditions=("3",),
        min_price=0,
        max_price=2000,
        attributes=(AttributeFilter(attribute_id="155", values=("13287",)),),
    )
    with patch.object(BrowserTransport, "_start", side_effect=AssertionError("HTTP must not start Chromium")):
        async with Yad2Client(
            http_settings, transport=HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
        ) as client:
            result = await client.search_pages(search, max_pages=3, refresh=True)
    assert result.complete and len(result.listings) == 2
    assert [request.url.params["pageNumber"] for request in requests] == ["1", "2"]
    for request in requests:
        assert request.url.host == "gw.yad2.co.il" and request.url.path == "/recommerce-feed/search"
        assert request.url.params["attr_155"] == "13287" and request.url.params["maxPrice"] == "2000"
        assert request.url.params["minPrice"] == "0" and request.url.params["conditions"] == "3"
        assert request.headers["cookie"] == "verified=original"
        assert request.headers["user-agent"] == "test-browser"
        assert request.url.params["scrollSessionId"] == requests[0].url.params["scrollSessionId"]
    assert not (await session_status(http_settings)).browser_lock.locked


async def test_collection_metadata_is_verified_and_each_http_request_is_paced(http_settings: Settings) -> None:
    paths: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.host == "www.yad2.co.il":
            return httpx2.Response(
                200,
                text='<script id="__NEXT_DATA__">'
                + json.dumps(
                    {"props": {"pageProps": {"collectionHebHandle": "ריהוט", "collectionEnglishHandle": "furniture"}}}
                )
                + "</script>",
            )
        if "/filters/" in request.url.path:
            assert "pageNumber" not in request.url.params and request.url.params["itemsPerPage"] == "10"
            return httpx2.Response(200, json=empty_filters())
        return httpx2.Response(200, json=feed())

    transport = HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
    with patch.object(transport.cache, "wait_turn", AsyncMock()) as pacing:
        url = market_url(MarketSearch(collection="furniture"))
        await transport.fetch_search(url)
        await transport.fetch_filters(url)
    assert paths == ["/market/collections/furniture", "/recommerce-feed/search/ריהוט", "/recommerce-feed/filters/ריהוט"]
    assert pacing.await_count == 3


async def test_rotated_cookies_survive_new_clients_without_losing_local_storage(http_settings: Settings) -> None:
    def rotate(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["cookie"] == "verified=original"
        return httpx2.Response(
            200,
            json=feed(),
            headers={"set-cookie": "verified=rotated; Domain=.yad2.co.il; Path=/; Secure; HttpOnly; SameSite=None"},
        )

    url = market_url(MarketSearch(query="RTX"))
    await HttpTransport(http_settings, transport=httpx2.MockTransport(rotate)).fetch_search(url)

    def observe(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["cookie"] == "verified=rotated"
        return httpx2.Response(200, json=feed())

    await HttpTransport(http_settings, transport=httpx2.MockTransport(observe)).fetch_search(url)
    state = BrowserState.model_validate_json((http_settings.data_dir / "browser-state.json").read_text())
    assert state.origins[0]["localStorage"] == [{"name": "keep", "value": "untouched"}]
    assert state.cookies[0].httpOnly and state.cookies[0].sameSite == "None"
    assert (http_settings.data_dir / "browser-state.json").stat().st_mode & 0o777 == 0o600


def test_cookie_domains_paths_expiry_and_flags_are_preserved() -> None:
    state = BrowserState(
        cookies=[
            StoredCookie(
                name="expired", value="secret", domain=".yad2.co.il", path="/", expires=1, secure=True, httpOnly=True
            ),
            StoredCookie(
                name="item",
                value="value",
                domain="www.yad2.co.il",
                path="/market/item",
                expires=-1,
                secure=True,
                httpOnly=False,
            ),
        ]
    )
    jar = state.cookie_jar()
    request = httpx2.Request("GET", "https://gw.yad2.co.il/recommerce-feed/search")
    jar.set_cookie_header(request)
    assert "cookie" not in request.headers
    request = httpx2.Request("GET", "https://www.yad2.co.il/market/item/123")
    jar.set_cookie_header(request)
    assert request.headers["cookie"] == "item=value"
    restored = state.with_cookies(jar)
    assert len(restored.cookies) == 1 and not restored.cookies[0].httpOnly


@pytest.mark.parametrize(
    "status,body,error",
    [
        (403, "denied", ChallengeRequired),
        (429, "busy", RateLimited),
        (200, "<title>Radware Bot Manager</title>", ChallengeRequired),
        (200, "not JSON", ParseError),
        (302, "", PageUnavailable),
        (503, "unavailable", PageUnavailable),
    ],
)
async def test_failures_are_not_retried_or_cached_and_preserve_session(
    http_settings: Settings, status: int, body: str, error: type[Exception]
) -> None:
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            status,
            text=body,
            headers={
                "retry-after": "600",
                "location": "https://example.com/",
                "set-cookie": "verified=bad; Domain=.yad2.co.il; Path=/",
            },
        )

    before = (http_settings.data_dir / "browser-state.json").read_text()
    async with Yad2Client(
        http_settings, transport=HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
    ) as client:
        with pytest.raises(error):
            await client.search(MarketSearch(query="RTX"))
        if error in {ChallengeRequired, RateLimited}:
            with (
                patch.object(
                    client.cache, "block", AsyncMock(side_effect=AssertionError("Do not extend an active cooldown"))
                ),
                pytest.raises(RateLimited),
            ):
                await client.search(MarketSearch(query="RTX"), refresh=True)
    assert calls == 1 and (http_settings.data_dir / "browser-state.json").read_text() == before
    budget = await Cache(http_settings.cache_dir).status()
    assert budget.cached_pages == 0
    if error in {ChallengeRequired, RateLimited}:
        assert budget.cooldown_remaining_seconds and budget.cooldown_remaining_seconds > 290
    assert not (await session_status(http_settings)).browser_lock.locked


async def test_missing_identity_requires_explicit_verification_without_network(http_settings: Settings) -> None:
    (http_settings.data_dir / "http-identity.json").unlink()

    def respond(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Missing session must not issue a request")

    with pytest.raises(ChallengeRequired, match="yad2 session"):
        await HttpTransport(http_settings, transport=httpx2.MockTransport(respond)).fetch_search(
            market_url(MarketSearch())
        )


async def test_busy_session_does_not_overwrite_another_owner(http_settings: Settings) -> None:
    path = http_settings.data_dir / "browser-owner.json"
    path.write_text("keep owner")
    with FileLock(http_settings.data_dir / "browser.lock"):
        with pytest.raises(BrowserError, match="in use"):
            await HttpTransport(http_settings).fetch_search(market_url(MarketSearch()))
        assert path.read_text() == "keep owner"


async def test_wrong_page_and_wrong_collection_are_schema_errors(http_settings: Settings) -> None:
    transport = HttpTransport(
        http_settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=feed()))
    )
    with pytest.raises(ParseError, match="different page"):
        parse_search(await transport.fetch_search(market_url(MarketSearch(query="RTX", page=2))))
    transport = HttpTransport(
        http_settings,
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(
                200,
                text='<script id="__NEXT_DATA__">{"props":{"pageProps":{"collectionEnglishHandle":"other","collectionHebHandle":"ריהוט"}}}</script>',
            )
        ),
    )
    with pytest.raises(ParseError, match="Collection metadata"):
        await transport.fetch_search(market_url(MarketSearch(collection="furniture")))


@pytest.mark.parametrize("page", [1, 2])
async def test_explicit_zero_match_feed_retains_requested_page_and_is_cached(
    http_settings: Settings, page: int
) -> None:
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        # Observed for q=pinstrop: page 0 denotes a genuinely empty result set.
        return httpx2.Response(
            200,
            json={
                "data": {
                    "items": [],
                    "currentPage": 0,
                    "totalItems": 0,
                    "totalPages": 0,
                }
            },
        )

    async with Yad2Client(
        http_settings, transport=HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
    ) as client:
        search = MarketSearch(query="pinstrop", page=page)
        result = await client.search(search)
        assert result.listings == () and result.page == page and result.total == result.total_pages == 0
        batch = await client.search_pages(search, max_pages=3)
        assert batch.stop_reason == "exhausted" and batch.complete == (page == 1)
        assert len(batch.pages) == 1 and batch.pages[0].cached and not batch.warnings
        assert calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"currentPage": None},
        {"currentPage": False},
        {"totalItems": False},
        {"totalItems": 1},
        {"totalPages": 1},
        {"items": [{"id": 1, "title": "Unexpected"}]},
    ],
)
async def test_zero_page_exception_rejects_incomplete_or_conflicting_feeds(
    http_settings: Settings, changes: dict[str, object]
) -> None:
    data: dict[str, object] = {"items": [], "currentPage": 0, "totalItems": 0, "totalPages": 0}
    data.update(changes)
    transport = HttpTransport(
        http_settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"data": data}))
    )
    with pytest.raises(ParseError):
        parse_search(await transport.fetch_search(market_url(MarketSearch(query="pinstrop"))))


def test_html_parser_preserves_raw_payload_and_rejects_missing_or_malformed_data(snapshot: PageSnapshot) -> None:
    body = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(snapshot.data) + "</script>"
    parsed = html_snapshot(snapshot.url, body)
    assert parsed.data == snapshot.data and parse_search(parsed).listings
    for invalid in ("<html>empty</html>", '<script id="__NEXT_DATA__">broken</script>', body + body):
        with pytest.raises(ParseError):
            html_snapshot(snapshot.url, invalid)
    with pytest.raises(ChallengeRequired):
        html_snapshot(snapshot.url, "<html>Are you for real?</html>")


async def test_unsupported_routes_use_browser_but_http_challenges_never_fall_back(
    http_settings: Settings, snapshot: PageSnapshot
) -> None:
    hybrid = HybridTransport(http_settings)
    hybrid.http = HttpTransport(http_settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(403)))
    with patch.object(hybrid.browser, "fetch", AsyncMock(return_value=snapshot)) as fetch:
        await hybrid.fetch("https://www.yad2.co.il/market")
        await hybrid.fetch_search("https://www.yad2.co.il/vehicles/cars")
        await hybrid.fetch_search("https://www.yad2.co.il/market/search?unverified=1")
        assert fetch.await_count == 3
        with pytest.raises(ChallengeRequired):
            await hybrid.fetch_search(snapshot.url)
        assert fetch.await_count == 3
    assert not supports_search("https://www.yad2.co.il/market/search?unverified=1")
    assert not supports_search("https://www.yad2.co.il/market/collections/furniture?q=sofa")


async def test_cancelled_http_read_releases_session_and_preserves_saved_state(http_settings: Settings) -> None:
    before = (http_settings.data_dir / "browser-state.json").read_text()
    entered = anyio.Event()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        entered.set()
        await anyio.sleep_forever()
        raise AssertionError("Unreachable")

    transport = HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(transport.fetch_search, market_url(MarketSearch(query="RTX")))
        await entered.wait()
        tasks.cancel_scope.cancel()
    assert not (await session_status(http_settings)).browser_lock.locked
    assert (http_settings.data_dir / "browser-state.json").read_text() == before


async def test_service_challenge_requires_verification_and_cached_results_remain_available(
    http_settings: Settings, snapshot: PageSnapshot
) -> None:
    challenged = False
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(403) if challenged else httpx2.Response(200, json=feed())

    def factory(settings: Settings) -> HybridTransport:
        hybrid = HybridTransport(settings)
        hybrid.http = HttpTransport(settings, transport=httpx2.MockTransport(respond))
        return hybrid

    with (
        anyio.fail_after(10),
        patch.object(BrowserTransport, "interactive", AsyncMock(return_value=snapshot)) as verify,
    ):
        async with anyio.create_task_group() as tasks:
            await tasks.start(serve_browser, http_settings, factory)
            async with Yad2Client(http_settings) as client:
                await client.search(MarketSearch(query="RTX"))
                challenged = True
                with pytest.raises(ChallengeRequired):
                    await client.search(MarketSearch(query="sofa"))
                assert (await client.search(MarketSearch(query="RTX"))).cached
                with pytest.raises(RateLimited):
                    await client.search(MarketSearch(query="sofa"), refresh=True)
                assert calls == 2 and verify.await_count == 0
                challenged = False
                await SharedBrowserTransport(http_settings).interactive(snapshot.url, 5)
                assert verify.await_count == 1
                await client.search(MarketSearch(query="sofa"), refresh=True)
                assert calls == 3
            assert await stop_service(http_settings)


async def test_shared_service_serializes_http_cookie_updates_across_clients(http_settings: Settings) -> None:
    values: list[str] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        values.append(request.headers["cookie"])
        await anyio.lowlevel.checkpoint()
        return httpx2.Response(
            200, json=feed(), headers={"set-cookie": f"verified={len(values)}; Domain=.yad2.co.il; Path=/; Secure"}
        )

    def factory(settings: Settings) -> HybridTransport:
        hybrid = HybridTransport(settings)
        hybrid.http = HttpTransport(settings, transport=httpx2.MockTransport(respond))
        return hybrid

    with anyio.fail_after(10), patch.object(BrowserTransport, "_start", side_effect=AssertionError("No browser")):
        async with anyio.create_task_group() as tasks:
            await tasks.start(serve_browser, http_settings, factory)
            async with anyio.create_task_group() as clients:
                for _ in range(2):
                    clients.start_soon(
                        SharedBrowserTransport(http_settings).fetch_search, market_url(MarketSearch(query="RTX"))
                    )
            status = await session_status(http_settings)
            assert status.service and not status.service.browser_running and status.service.transport == "http"
            assert values == ["verified=original", "verified=1"]
            assert await stop_service(http_settings)


async def test_raw_inspection_cache_is_separate_from_synthetic_api_feed(
    http_settings: Settings, snapshot: PageSnapshot
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "gw.yad2.co.il":
            return httpx2.Response(200, json=feed())
        return httpx2.Response(200, text='<script id="__NEXT_DATA__">' + json.dumps(snapshot.data) + "</script>")

    async with Yad2Client(
        http_settings, transport=HttpTransport(http_settings, transport=httpx2.MockTransport(respond))
    ) as client:
        search = await client.search(MarketSearch(query="RTX"))
        raw = await client.page(search.source_url)
        assert raw.data == snapshot.data and not raw.cached
        assert (await client.search(MarketSearch(query="RTX"))).cached
        assert (await client.page(search.source_url)).cached
