"""Bounded, sequential feed traversal with explicit coverage and stop reasons."""

from typing import Literal, Protocol
from urllib.parse import parse_qs, parse_qsl, urlsplit

from yad2_client.errors import ParseError
from yad2_client.models import Listing, SearchBatch, SearchPage, SearchResult
from yad2_client.urls import validate_url


class SearchPageLoader(Protocol):
    async def __call__(self, url: str, *, refresh: bool = False) -> SearchResult: ...


def _feed_key(url: str, page_key: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    parsed = urlsplit(validate_url(url))
    return parsed.path, tuple(sorted((key, value) for key, value in parse_qsl(parsed.query) if key != page_key))


async def collect_pages(
    load: SearchPageLoader, url: str, *, max_pages: int, max_items: int, refresh: bool
) -> SearchBatch:
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 20:
        raise ValueError("max_pages must be between 1 and 20")
    if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= 1000:
        raise ValueError("max_items must be between 1 and 1000")
    url = validate_url(url)
    section = urlsplit(url).path.strip("/").split("/")[0]
    page_key = "pageNumber" if section == "market" else "page"
    expected = int(parse_qs(urlsplit(url).query).get(page_key, ["1"])[0])
    if not 1 <= expected <= 10000:
        raise ValueError("Starting page must be between 1 and 10000")
    first_page = expected
    seen_urls: set[str] = set()
    listings: dict[str, Listing] = {}
    pages: list[SearchPage] = []
    duplicates = 0
    warnings: list[str] = []
    reason: Literal["exhausted", "page_limit", "item_limit", "pagination_unknown"] = "page_limit"
    next_url: str | None = url
    for _ in range(max_pages):
        if next_url is None or next_url in seen_urls:
            raise ParseError("Feed pagination repeated a page URL")
        seen_urls.add(next_url)
        result = await load(next_url, refresh=refresh)
        if result.page != expected or _feed_key(result.source_url, page_key) != _feed_key(url, page_key):
            raise ParseError("Feed returned a different page than requested; refusing an incomplete traversal")
        pages.append(
            SearchPage(
                source_url=result.source_url,
                page=result.page,
                fetched_at=result.fetched_at,
                cached=result.cached,
                total=result.total,
                total_pages=result.total_pages,
                warnings=result.warnings,
            )
        )
        warnings.extend(result.warnings)
        truncated = False
        for listing in result.listings:
            if listing.url in listings:
                duplicates += 1
            elif len(listings) < max_items:
                listings[listing.url] = listing
            else:
                truncated = True
        next_url = result.next_url
        if truncated:
            reason = "item_limit"
            next_url = result.source_url  # Resume this page; the caller should deduplicate already seen items.
            break
        if next_url is None:
            exhausted = (result.total_pages is not None and result.page >= result.total_pages) or (
                result.total == 0 and not result.listings
            )
            reason = "exhausted" if exhausted else "pagination_unknown"
            break
        next_url = validate_url(next_url)
        if _feed_key(next_url, page_key) != _feed_key(url, page_key):
            raise ParseError("Pagination changed the feed filters or path")
        expected += 1
        if expected > 10000:
            raise ParseError("Pagination exceeded the supported page range")
        if parse_qs(urlsplit(next_url).query).get(page_key) != [str(expected)]:
            raise ParseError("Pagination did not advance to the next page")
        if len(listings) >= max_items:
            reason = "item_limit"
            break
    totals = {page.total for page in pages if page.total is not None}
    if len(totals) > 1:
        warnings.append("Feed totals changed during pagination; coverage is uncertain")
    if len({page.total_pages for page in pages}) > 1:
        warnings.append("Feed page counts changed during pagination; coverage is uncertain")
    if totals and max(totals) > len(listings) and reason == "exhausted":
        warnings.append("Fewer unique listings were observed than the feed's reported total")
    if totals and min(totals) < len(listings):
        warnings.append("More unique listings were observed than the feed's reported total")
    complete = first_page == 1 and reason == "exhausted" and not warnings
    return SearchBatch(
        listings=tuple(listings.values()),
        pages=tuple(pages),
        next_url=next_url,
        stop_reason=reason,
        complete=complete,
        duplicates_removed=duplicates,
        warnings=tuple(warnings),
    )
