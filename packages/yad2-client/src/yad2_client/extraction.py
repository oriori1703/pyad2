"""Remove observed page/API wrappers without constructing or validating domain models."""

from urllib.parse import urlsplit

from pydantic import JsonValue

from yad2_client.errors import ParseError
from yad2_client.models import PageSnapshot, PayloadKind, RawPayload


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


def page_props(snapshot: PageSnapshot) -> dict[str, JsonValue]:
    return obj(obj(snapshot.data.get("props")).get("pageProps"))


def query_data(props: dict[str, JsonValue]) -> list[tuple[list[JsonValue], dict[str, JsonValue]]]:
    return [
        (array(obj(query).get("queryKey")), obj(obj(obj(query).get("state")).get("data")))
        for query in array(obj(props.get("dehydratedState")).get("queries"))
    ]


def _payload(snapshot: PageSnapshot, kind: PayloadKind, data: dict[str, JsonValue]) -> RawPayload:
    return RawPayload(kind=kind, url=snapshot.url, fetched_at=snapshot.fetched_at, data=data, cached=snapshot.cached)


def extract_api(snapshot: PageSnapshot, kind: PayloadKind) -> RawPayload:
    """Unwrap the observed API {data: object} envelope, including filter responses."""
    data = snapshot.data.get("data")
    if not isinstance(data, dict):
        raise ParseError("API response has no data object")
    return _payload(snapshot, kind, data)


def extract_search(snapshot: PageSnapshot) -> RawPayload:
    props = page_props(snapshot)
    for key in ("initialFeed", "feed"):
        if isinstance(props.get(key), dict):
            return _payload(snapshot, "search", obj(props[key]))
    for query_key, data in query_data(props):
        if query_key and isinstance(query_key[0], str) and ("feed" in query_key[0] or query_key[0] == "search"):
            if any(name in data for name in ("items", "yad2", "ads")):
                return _payload(snapshot, "search", data)
            pages = array(data.get("pages"))
            if pages and isinstance(pages[0], dict):
                return _payload(snapshot, "search", pages[0])
    raise ParseError("No recognized listing feed. Use a search/collection URL; the page schema may have changed.")


def extract_listing(snapshot: PageSnapshot) -> RawPayload:
    props = page_props(snapshot)
    expected = urlsplit(snapshot.url).path.rstrip("/").split("/")[-1]
    candidates = [obj(props.get("item"))]
    for key, data in query_data(props):
        if any(isinstance(part, str) and ("item" in part.lower() or part == "ad") for part in key):
            candidates.extend([data, obj(data.get("data")), obj(data.get("item"))])
    for candidate in candidates:
        if (text(candidate.get("token")) or text(candidate.get("id"))) == expected:
            return _payload(snapshot, "listing", candidate)
    raise ParseError(f"Requested listing {expected} was not present in the page payload")
