from datetime import UTC, datetime

import pytest

from yad2_client._enum import CatalogId
from yad2_client.catalog_codegen import canonical_snapshot, enum_name, merged_attributes, named_entries, render_enums
from yad2_client.catalog_models import AttributeDefinition, CatalogSnapshot, OptionDefinition, ProductDefinition


def test_enum_ids_keep_bilingual_labels_and_string_behavior() -> None:
    class Example(CatalogId):
        COMPONENTS = "285", "Computer Components", "רכיבי מחשב"

    assert Example("285") is Example.COMPONENTS
    value: str = Example.COMPONENTS
    assert value == "285"
    assert Example.COMPONENTS.label_he == "רכיבי מחשב"
    assert Example.COMPONENTS.label_en == "Computer Components"


def test_codegen_preserves_scope_and_rejects_conflicting_metadata() -> None:
    option = OptionDefinition(
        id="13287", name="GRAPHICS_CARD", label_en="Graphics card", label_he="כרטיס מסך", attribute_id="8"
    )
    attribute = AttributeDefinition(
        id="8",
        name="COMPONENT_TYPE",
        label_en="Component type",
        label_he="סוג רכיב",
        input_type="array",
        options=(option,),
    )
    first = ProductDefinition(
        id="285", name="COMPONENTS", label_en="Components", label_he="רכיבים", attributes=(attribute,)
    )
    second = first.model_copy(update={"id": "286", "name": "COMPUTERS"})
    catalog = CatalogSnapshot(
        fetched_at=datetime.now(UTC), source_url="https://example.com/catalog", products=(second, first)
    )
    enums, options = render_enums(catalog)
    assert enums.index("COMPONENTS =") < enums.index("COMPUTERS =")
    assert "class ComponentTypeOption" in options
    assert "GRAPHICS_CARD =" in options
    assert len(merged_attributes(catalog)[0].options) == 1
    text_attribute = attribute.model_copy(update={"input_type": "string", "required": True})
    scoped = canonical_snapshot(
        catalog.model_copy(update={"products": (first, second.model_copy(update={"attributes": (text_attribute,)}))})
    )
    assert scoped.products[0].attributes[0].input_type == "array"
    assert scoped.products[1].attributes[0].input_type == "string"
    assert scoped.products[1].attributes[0].required is True
    bad = attribute.model_copy(update={"label_en": "different"})
    conflicting = catalog.model_copy(update={"products": (first, second.model_copy(update={"attributes": (bad,)}))})
    with pytest.raises(ValueError, match="Conflicting"):
        render_enums(conflicting)


def test_enum_identifiers_are_valid_and_literals_are_escaped() -> None:
    assert enum_name("16 GB", "123") == "VALUE_16_GB"
    assert enum_name("כרטיס מסך", "123").isidentifier()
    product = ProductDefinition(id="1", name="TYPE", label_en="quoted '\n name", label_he="שם")
    snapshot = CatalogSnapshot(fetched_at=datetime.now(UTC), source_url="https://example.com", products=(product,))
    source, _ = render_enums(snapshot)
    compile(source, "generated.py", "exec")


def test_empty_catalog_is_not_a_valid_refresh() -> None:
    with pytest.raises(ValueError, match="empty"):
        CatalogSnapshot(fetched_at=datetime.now(UTC), source_url="https://example.com", products=())


def test_duplicate_names_do_not_alias_members_or_collide_with_existing_suffixes() -> None:
    entries = tuple(
        ProductDefinition(id=identifier, name=name, label_en=name, label_he=name)
        for identifier, name in (("1", "TYPE"), ("2", "TYPE"), ("3", "TYPE_1"))
    )
    named = named_entries(entries)
    assert len({entry.name for entry in named}) == 3
    assert named_entries(reversed(entries)) == named
