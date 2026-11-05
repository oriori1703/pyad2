from datetime import UTC, datetime

import pytest

from yad2_client.errors import ChallengeRequired, ParseError
from yad2_client.models import Listing, SearchResult
from yad2_client.pagination import collect_pages
from yad2_client.urls import with_page

URL = "https://www.yad2.co.il/market/search?q=RTX&pageNumber=1"


def result(page: int, ids: tuple[str, ...], *, total: int = 3, total_pages: int | None = 2) -> SearchResult:
    return SearchResult(
        listings=tuple(
            Listing(id=id, url=f"https://www.yad2.co.il/market/item/{id}", section="market", title=id) for id in ids
        ),
        source_url=with_page(URL, page),
        fetched_at=datetime.now(UTC),
        page=page,
        total=total,
        total_pages=total_pages,
        next_url=with_page(URL, page + 1) if total_pages and page < total_pages else None,
    )


async def test_sequential_pages_deduplicate_and_report_full_coverage() -> None:
    visited: list[str] = []

    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        visited.append(url)
        assert refresh
        return result(len(visited), ("1", "2") if len(visited) == 1 else ("2", "3"))

    batch = await collect_pages(load, URL, max_pages=3, max_items=20, refresh=True)
    assert visited == [URL, with_page(URL, 2)]
    assert [listing.id for listing in batch.listings] == ["1", "2", "3"]
    assert batch.duplicates_removed == 1 and batch.complete
    assert batch.stop_reason == "exhausted" and batch.next_url is None


@pytest.mark.parametrize("max_pages,max_items,reason", [(1, 20, "page_limit"), (2, 1, "item_limit")])
async def test_budgets_stop_before_an_extra_request(max_pages: int, max_items: int, reason: str) -> None:
    calls = 0

    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        nonlocal calls
        calls += 1
        return result(1, ("1", "2"))

    batch = await collect_pages(load, URL, max_pages=max_pages, max_items=max_items, refresh=False)
    assert calls == 1 and not batch.complete
    assert batch.stop_reason == reason
    assert batch.next_url is not None


async def test_failures_propagate_without_returning_a_partial_success() -> None:
    calls = 0

    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ChallengeRequired("Verify")
        return result(1, ("1",))

    with pytest.raises(ChallengeRequired):
        await collect_pages(load, URL, max_pages=3, max_items=20, refresh=False)
    assert calls == 2


async def test_wrong_pages_and_unknown_pagination_cannot_claim_complete_coverage() -> None:
    async def wrong(url: str, *, refresh: bool = False) -> SearchResult:
        return result(2, ("1",))

    with pytest.raises(ParseError, match="different page"):
        await collect_pages(wrong, URL, max_pages=3, max_items=20, refresh=False)

    async def unknown(url: str, *, refresh: bool = False) -> SearchResult:
        return result(1, ("1",), total=1, total_pages=None)

    batch = await collect_pages(unknown, URL, max_pages=3, max_items=20, refresh=False)
    assert not batch.complete and batch.stop_reason == "pagination_unknown"


@pytest.mark.parametrize(
    "next_url",
    [
        URL,
        with_page(URL, 3),
        with_page(URL, 2).replace("RTX", "sofa"),
    ],
)
async def test_invalid_continuations_fail_before_another_navigation(next_url: str) -> None:
    calls = 0

    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        nonlocal calls
        calls += 1
        return result(1, ("1",)).model_copy(update={"next_url": next_url})

    with pytest.raises(ParseError, match="Pagination"):
        await collect_pages(load, URL, max_pages=3, max_items=20, refresh=False)
    assert calls == 1


async def test_missing_continuation_with_remaining_pages_is_unknown() -> None:
    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        return result(1, ("1",)).model_copy(update={"next_url": None})

    batch = await collect_pages(load, URL, max_pages=3, max_items=20, refresh=False)
    assert not batch.complete and batch.stop_reason == "pagination_unknown"


@pytest.mark.parametrize("scenario", ["totals", "pages", "warnings", "missing", "extra", "start"])
async def test_uncertain_coverage_never_claims_complete_results(scenario: str) -> None:
    calls = 0

    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        nonlocal calls
        calls += 1
        page = 2 if scenario == "start" else calls
        current = result(page, (str(page),), total=2)
        if scenario == "totals":
            return current.model_copy(update={"total": calls})
        if scenario == "pages" and page == 1:
            return current.model_copy(update={"total_pages": 3})
        if scenario == "warnings":
            return current.model_copy(update={"warnings": ("Unparseable entry",)})
        if scenario in {"missing", "extra"}:
            return current.model_copy(update={"total": 3 if scenario == "missing" else 1})
        return current

    batch = await collect_pages(
        load, with_page(URL, 2) if scenario == "start" else URL, max_pages=3, max_items=20, refresh=False
    )
    assert batch.stop_reason == "exhausted" and not batch.complete


async def test_explicit_empty_feed_is_complete() -> None:
    async def load(url: str, *, refresh: bool = False) -> SearchResult:
        return result(1, (), total=0, total_pages=0)

    assert (await collect_pages(load, URL, max_pages=3, max_items=20, refresh=False)).complete
