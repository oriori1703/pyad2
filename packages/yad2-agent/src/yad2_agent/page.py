"""Bounded structured page inspection for sections without a normalized adapter."""

import json

from pydantic import JsonValue

from yad2_client.models import Model, PageSnapshot


class PageView(Model):
    source_url: str
    path: str
    keys: tuple[str, ...]
    value: JsonValue
    omitted: bool
    serialized_chars: int
    cached: bool


def inspect_page(snapshot: PageSnapshot, path: str = "", max_chars: int = 16000) -> PageView:
    value: JsonValue = snapshot.data
    for segment in path.split(".") if path else ():
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        elif isinstance(value, list) and segment.isdigit() and int(segment) < len(value):
            value = value[int(segment)]
        else:
            raise ValueError(f"No data at path {path!r}; inspect its parent to discover available keys")
    keys = (
        tuple(value)
        if isinstance(value, dict)
        else tuple(map(str, range(len(value))))
        if isinstance(value, list)
        else ()
    )
    size = len(json.dumps(value, ensure_ascii=False))
    return PageView(
        source_url=snapshot.url,
        path=path,
        keys=keys[:200],
        value=value if size <= max_chars else None,
        omitted=size > max_chars,
        serialized_chars=size,
        cached=snapshot.cached,
    )
