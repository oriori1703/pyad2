"""Pure parsers for observed marketplace, property and vehicle Next.js payloads."""

import math
from urllib.parse import parse_qs, urlsplit

from pydantic import JsonValue

from yad2_client.attributes import normalize_attributes
from yad2_client.errors import ParseError
from yad2_client.models import Category, CategoryResult, Listing, ListingResult, PageSnapshot, SearchResult
from yad2_client.urls import ORIGIN, with_page


def obj(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def array(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def text(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def label(value: JsonValue | None) -> str | None:
    data = obj(value)
    return text(data.get("textHeb")) or text(data.get("text")) or text(value)


def number(value: JsonValue | None) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (float, int, str)):
        try:
            result = float(value.replace(",", "").replace("₪", "").strip() if isinstance(value, str) else value)
        except ValueError:
            return None
        return result if math.isfinite(result) else None
    return None


def integer(value: JsonValue | None) -> int | None:
    result = number(value)
    return int(result) if result is not None and result >= 0 and result.is_integer() else None


def page_props(snapshot: PageSnapshot) -> dict[str, JsonValue]:
    return obj(obj(snapshot.data.get("props")).get("pageProps"))


def query_data(props: dict[str, JsonValue]) -> list[tuple[list[JsonValue], dict[str, JsonValue]]]:
    return [
        (array(obj(query).get("queryKey")), obj(obj(obj(query).get("state")).get("data")))
        for query in array(obj(props.get("dehydratedState")).get("queries"))
    ]


def parse_listing(data: dict[str, JsonValue], section: str) -> Listing:
    identifier = text(data.get("token")) or text(data.get("id"))
    if not identifier:
        raise ParseError("Listing has no ID or token; Yad2 may have changed its schema")
    address, meta = obj(data.get("address")), obj(data.get("metaData"))
    details = obj(data.get("additionalDetails"))
    city = label(address.get("city"))
    title = text(data.get("title"))
    if not title:
        parts = (
            (label(data.get("manufacturer")), label(data.get("model")), label(data.get("subModel")))
            if section == "vehicles"
            else (label(details.get("property")), label(address.get("street")), city)
        )
        title = " · ".join(part for part in parts if part)
    if not title:
        raise ParseError(f"Listing {identifier} has no recognizable title")
    attributes = dict(details)
    for item in array(data.get("attributeKeys")):
        attribute = obj(item)
        name = text(attribute.get("attributeTextEng")) or text(attribute.get("attributeTextHeb"))
        if name:
            attributes[name] = attribute.get("optionText")
    for key in ("vehicleDates", "engineType", "engineVolume", "hand", "km", "inProperty", "shippingType", "giveAway"):
        if key in data:
            attributes[key] = data[key]
    images = array(data.get("images")) or array(meta.get("images"))
    if not images and meta.get("coverImage"):
        images = [meta["coverImage"]]
    customer = obj(data.get("customer"))
    return Listing(
        id=identifier,
        url=f"{ORIGIN}/{section}/item/{identifier}",
        section=section,
        title=title,
        price=number(data.get("price")),
        description=text(data.get("description")) or text(meta.get("description")),
        city=city,
        city_id=text(obj(address.get("city")).get("id")),
        area=label(address.get("area")),
        condition=label(data.get("condition")),
        product_type=label(data.get("productType")),
        product_type_id=text(obj(data.get("productType")).get("id")),
        images=tuple(image for image in images if isinstance(image, str) and image.startswith("https://")),
        attributes=attributes,
        normalized_attributes=normalize_attributes(
            array(data.get("attributeKeys")), text(obj(data.get("productType")).get("id"))
        ),
        seller_name=text(customer.get("name")) or text(customer.get("agencyName")),
    )


def _feed(props: dict[str, JsonValue]) -> dict[str, JsonValue]:
    for key in ("initialFeed", "feed"):
        if isinstance(props.get(key), dict):
            return obj(props[key])
    for query_key, data in query_data(props):
        if query_key and isinstance(query_key[0], str) and ("feed" in query_key[0] or query_key[0] == "search"):
            if any(name in data for name in ("items", "yad2", "ads")):
                return data
            pages = array(data.get("pages"))
            if pages and isinstance(pages[0], dict):
                return pages[0]
    raise ParseError("No recognized listing feed. Use a search/collection URL; the page schema may have changed.")


def parse_search(snapshot: PageSnapshot) -> SearchResult:
    feed = _feed(page_props(snapshot))
    section = urlsplit(snapshot.url).path.strip("/").split("/")[0]
    rows: list[JsonValue] = []
    # Explicit feed groups avoid accidentally collecting recommended listings or filter options.
    for key in ("items", "yad2", "ads", "platinum", "boost", "solo"):
        rows.extend(array(feed.get(key)))
    if not any(isinstance(feed.get(key), list) for key in ("items", "yad2", "ads")):
        raise ParseError("Recognized feed has no listing array; refusing to report a false empty result")
    listings: dict[str, Listing] = {}
    warnings: list[str] = []
    for row in rows:
        try:
            listing = parse_listing(obj(row), section)
        except ParseError as exc:
            warnings.append(str(exc))
        else:
            listings.setdefault(listing.id, listing)
    if rows and not listings:
        raise ParseError(f"None of the {len(rows)} feed entries could be parsed")
    pagination = obj(feed.get("pagination"))
    total = integer(feed.get("totalItems")) if "totalItems" in feed else integer(pagination.get("total"))
    total_pages = integer(feed.get("totalPages"))
    if total_pages is None:
        total_pages = integer(pagination.get("totalPages")) or integer(pagination.get("pages"))
    query = parse_qs(urlsplit(snapshot.url).query)
    requested_page = query.get("pageNumber" if section == "market" else "page", ["1"])[0]
    page = integer(feed.get("currentPage")) or integer(requested_page) or 1
    return SearchResult(
        listings=tuple(listings.values()),
        source_url=snapshot.url,
        fetched_at=snapshot.fetched_at,
        cached=snapshot.cached,
        total=total,
        page=page,
        total_pages=total_pages,
        next_url=with_page(snapshot.url, page + 1) if total_pages is not None and page < total_pages else None,
        warnings=tuple(warnings),
    )


def parse_item(snapshot: PageSnapshot) -> ListingResult:
    props = page_props(snapshot)
    section = urlsplit(snapshot.url).path.strip("/").split("/")[0]
    expected = urlsplit(snapshot.url).path.rstrip("/").split("/")[-1]
    candidates = [obj(props.get("item"))]
    for key, data in query_data(props):
        if any(isinstance(part, str) and ("item" in part.lower() or part == "ad") for part in key):
            candidates.extend([data, obj(data.get("data")), obj(data.get("item"))])
    for candidate in candidates:
        if (text(candidate.get("token")) or text(candidate.get("id"))) == expected:
            return ListingResult(
                listing=parse_listing(candidate, section),
                source_url=snapshot.url,
                fetched_at=snapshot.fetched_at,
                cached=snapshot.cached,
            )
    raise ParseError(f"Requested listing {expected} was not present in the page payload")


def parse_categories(snapshot: PageSnapshot, query: str = "") -> CategoryResult:
    categories: dict[str, Category] = {}
    for link in snapshot.links:
        path = urlsplit(link.url).path
        if not path.startswith("/market/collections/") or not link.title:
            continue
        slug = path.removeprefix("/market/collections/").strip("/")
        if query.casefold() not in f"{slug} {link.title}".casefold():
            continue
        categories.setdefault(slug, Category(slug=slug, title=link.title, url=f"{ORIGIN}{path}"))
    return CategoryResult(
        categories=tuple(sorted(categories.values(), key=lambda category: category.slug)),
        source_url=snapshot.url,
        fetched_at=snapshot.fetched_at,
        cached=snapshot.cached,
    )
