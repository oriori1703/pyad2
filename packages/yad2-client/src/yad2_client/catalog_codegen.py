"""Deterministic source generation for the checked-in Yad2 taxonomy enums."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from yad2_client.catalog_models import AttributeDefinition, CatalogEntry, CatalogSnapshot, OptionDefinition


def enum_name(label: str, identifier: str) -> str:
    name = re.sub(r"[^\w]+", "_", unicodedata.normalize("NFKC", label), flags=re.UNICODE).strip("_").upper()
    if not name or name[0].isdigit():
        name = f"VALUE_{name or identifier}"
    if not name.isidentifier():
        raise ValueError(f"Cannot generate an identifier for {label!r}")
    return name


def named_entries(entries: Iterable[CatalogEntry]) -> tuple[CatalogEntry, ...]:
    ordered = sorted(entries, key=lambda entry: int(entry.id))
    if len({entry.id for entry in ordered}) != len(ordered):
        raise ValueError("Duplicate catalog IDs")
    while True:
        counts = Counter(entry.name for entry in ordered)
        if all(count == 1 for count in counts.values()):
            return tuple(ordered)
        ordered = [
            entry.model_copy(update={"name": f"{entry.name}_{entry.id}"}) if counts[entry.name] > 1 else entry
            for entry in ordered
        ]


def merged_attributes(snapshot: CatalogSnapshot) -> tuple[AttributeDefinition, ...]:
    """Union ID/label pairs for code generation; input types and required flags remain product-scoped."""
    attributes: dict[str, AttributeDefinition] = {}
    for product in snapshot.products:
        for attribute in product.attributes:
            previous = attributes.get(attribute.id)
            options: dict[str, OptionDefinition] = (
                {option.id: option for option in previous.options} if previous else {}
            )
            if previous and (previous.label_en, previous.label_he) != (
                attribute.label_en,
                attribute.label_he,
            ):
                raise ValueError(f"Conflicting definitions for attribute {attribute.id}")
            for option in attribute.options:
                if option.id in options and options[option.id] != option:
                    raise ValueError(f"Conflicting labels for option {option.id} of attribute {attribute.id}")
                options[option.id] = option
            attributes[attribute.id] = attribute.model_copy(update={"options": tuple(options.values())})
    return tuple(sorted(attributes.values(), key=lambda attribute: int(attribute.id)))


def _class(name: str, entries: Iterable[CatalogEntry]) -> str:
    lines = [f"class {name}(CatalogId):"]
    for entry in named_entries(entries):
        if not entry.name.isidentifier() or entry.name.startswith("_"):
            raise ValueError(f"Invalid enum member name {entry.name!r}")
        lines.append(f"    {entry.name} = ({entry.id!r}, {entry.label_en!r}, {entry.label_he!r})")
    if len(lines) == 1:
        lines.append("    pass")
    return "\n".join(lines)


def render_enums(snapshot: CatalogSnapshot) -> tuple[str, str]:
    """Return product/attribute enum source and per-attribute option enum source."""
    attributes = merged_attributes(snapshot)
    header = '"""Generated from Yad2 catalog metadata; regenerate with tools/update_catalog.py."""\n\n'
    enums = header + "from yad2_client._enum import CatalogId\n\n\n"
    enums += _class("ProductType", snapshot.products) + "\n\n\n" + _class("Attribute", attributes) + "\n"
    options = header + "from yad2_client._enum import CatalogId\nfrom yad2_client.enums import Attribute\n\n\n"
    registry: list[str] = []
    for attribute in named_entries(attributes):
        definition = next(item for item in attributes if item.id == attribute.id)
        if not definition.options:
            continue
        class_name = "".join(part.title() for part in attribute.name.split("_")) + "Option"
        options += _class(class_name, definition.options) + "\n\n\n"
        registry.append(f"    Attribute.{attribute.name}: {class_name},")
    options += "OPTION_ENUMS: dict[Attribute, type[CatalogId]] = {\n" + "\n".join(registry) + "\n}\n"
    return enums, options


def canonical_snapshot(snapshot: CatalogSnapshot) -> CatalogSnapshot:
    """Use the same globally disambiguated names in lookup results and generated enums."""
    attributes = merged_attributes(snapshot)
    attribute_names = {entry.id: entry.name for entry in named_entries(attributes)}
    option_names = {
        attribute.id: {entry.id: entry.name for entry in named_entries(attribute.options)} for attribute in attributes
    }
    product_names = {entry.id: entry.name for entry in named_entries(snapshot.products)}
    return snapshot.model_copy(
        update={
            "products": tuple(
                product.model_copy(
                    update={
                        "name": product_names[product.id],
                        "attributes": tuple(
                            attribute.model_copy(
                                update={
                                    "name": attribute_names[attribute.id],
                                    "options": tuple(
                                        option.model_copy(update={"name": option_names[attribute.id][option.id]})
                                        for option in attribute.options
                                    ),
                                }
                            )
                            for attribute in product.attributes
                        ),
                    }
                )
                for product in sorted(snapshot.products, key=lambda product: int(product.id))
            )
        }
    )
