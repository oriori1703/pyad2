"""URL construction and validation for read-only public Yad2 pages."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from yad2_client.models import MarketSearch

ORIGIN = "https://www.yad2.co.il"


def validate_url(url: str) -> str:
    if any(ord(char) < 32 for char in url) or "\\" in url:
        raise ValueError("Invalid characters in URL")
    parsed = urlsplit(ORIGIN + url if url.startswith("/") and not url.startswith("//") else url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.yad2.co.il", "yad2.co.il"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Use an HTTPS URL on www.yad2.co.il")
    return urlunsplit(("https", "www.yad2.co.il", parsed.path or "/", parsed.query, ""))


def market_url(search: MarketSearch) -> str:
    path = f"/market/collections/{search.collection}" if search.collection else "/market/search"
    params = {"q": search.query.strip(), "sortOption": search.sort.value, "pageNumber": str(search.page)}
    for key, values in (
        ("cities", search.cities),
        ("areas", search.areas),
        ("productTypes", search.product_types),
        ("conditions", search.conditions),
    ):
        if values:
            params[key] = ",".join(values)
    for key, price in (("minPrice", search.min_price), ("maxPrice", search.max_price)):
        if price is not None:
            params[key] = str(price)
    for attribute in search.attributes:
        params[f"attr_{attribute.attribute_id}"] = ",".join(attribute.values)
    return f"{ORIGIN}{path}?{urlencode({key: value for key, value in params.items() if value})}"


def listing_url(value: str) -> str:
    if re.fullmatch(r"[0-9]+", value):
        return f"{ORIGIN}/market/item/{value}"
    url = validate_url(value)
    if not re.fullmatch(r"/[^/]+/item/[a-zA-Z0-9_-]+/?", urlsplit(url).path):
        raise ValueError("Supply a market listing ID or a full Yad2 /SECTION/item/ID URL")
    return urlunsplit((*urlsplit(url)[:3], "", ""))


def with_page(url: str, page: int) -> str:
    parsed = urlsplit(url)
    key = "pageNumber" if parsed.path.startswith("/market/") else "page"
    pairs = [(name, value) for name, value in parse_qsl(parsed.query) if name != key]
    return urlunsplit(parsed._replace(query=urlencode([*pairs, (key, str(page))])))
