"""Normalize explicit listing attributes, retaining raw values and mapping provenance."""

import math
import re
from functools import lru_cache
from typing import Literal

from pydantic import JsonValue

from yad2_client.catalog import load_catalog
from yad2_client.catalog_models import ProductDefinition
from yad2_client.models import ListingAttribute

Unit = Literal["cm", "GB", "MHz", "kg"]


@lru_cache(maxsize=1)
def _products() -> dict[str, ProductDefinition]:
    return {product.id: product for product in load_catalog().products}


def _text(value: JsonValue | None) -> str | None:
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _measurement(raw: JsonValue, label: str) -> tuple[float | None, Unit | None]:
    value = _text(raw) if not isinstance(raw, float) else str(raw)
    if value is None:
        return None, None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([^\d]*)", value)
    if match is None:
        return None, None
    suffix = match[2].strip().casefold()
    units: dict[str, tuple[Unit, float]] = {
        "cm": ("cm", 1),
        'ס"מ': ("cm", 1),
        "ס״מ": ("cm", 1),
        "mm": ("cm", 0.1),
        'מ"מ': ("cm", 0.1),
        "m": ("cm", 100),
        "מטר": ("cm", 100),
        "gb": ("GB", 1),
        "tb": ("GB", 1000),
        "mhz": ("MHz", 1),
        "ghz": ("MHz", 1000),
        "kg": ("kg", 1),
        'ק"ג': ("kg", 1),
        "ק״ג": ("kg", 1),
    }
    if not suffix:
        explicit = {value.strip().casefold() for value in re.findall(r"\(([^)]+)\)", label)}
        measurements = {units[value] for value in explicit if value in units}
        if len(measurements) != 1:
            return None, None
        suffix = next(value for value in explicit if value in units)
    if suffix not in units:
        return None, None
    unit, multiplier = units[suffix]
    numeric = float(match[1]) * multiplier
    return (round(numeric, 9), unit) if math.isfinite(numeric) else (None, None)


def normalize_attributes(rows: list[JsonValue], product_type: str | None) -> tuple[ListingAttribute, ...]:
    product = _products().get(product_type or "")
    definitions = product.attributes if product else ()
    normalized: list[ListingAttribute] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = _text(row.get("attributeId"))
        english, hebrew = _text(row.get("attributeTextEng")), _text(row.get("attributeTextHeb"))
        labels = {value.strip().casefold() for value in (english, hebrew) if value}
        matches = [
            definition
            for definition in definitions
            if (
                definition.id == identifier
                if identifier
                else bool(labels & {definition.label_en.casefold(), definition.label_he.casefold()})
            )
        ]
        definition = matches[0] if len(matches) == 1 else None
        source: Literal["listing", "catalog"] | None = "listing" if identifier else "catalog" if definition else None
        raw = row.get("optionText")
        unit_labels = " ".join(
            value
            for value in (
                hebrew,
                english,
                definition.label_he if definition else None,
                definition.label_en if definition else None,
            )
            if value
        )
        numeric, unit = _measurement(raw, unit_labels)
        normalized.append(
            ListingAttribute(
                attribute_id=identifier or (definition.id if definition else None),
                name=definition.name if definition else None,
                label_en=english,
                label_he=hebrew,
                option_id=_text(row.get("optionId")),
                raw_value=raw,
                numeric_value=numeric,
                unit=unit,
                id_source=source,
            )
        )
    return tuple(normalized)
