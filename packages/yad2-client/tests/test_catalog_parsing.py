from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from yad2_client.catalog_codegen import canonical_snapshot, merged_attributes
from yad2_client.catalog_parsing import parse_catalog
from yad2_client.errors import ParseError

PRODUCTS: JsonValue = {
    "data": [
        {"id": 285, "heName": "רכיבי מחשב", "enName": "Computer Components", "adminCode": 6},
        {"id": 325, "heName": "מחשבים שולחניים", "enName": "Desktop Computers"},
    ],
    "message": "OK",
}
FETCHED = datetime(2026, 11, 5, tzinfo=UTC)


def attribute(option_id: int, label: str) -> JsonValue:
    return {
        "id": 24,
        "heName": "יצרן",
        "enName": "manufacture",
        "inputType": "array",
        "required": False,
        "values": [{"id": option_id, "name": label, "flexible": False}],
    }


def test_product_specific_options_are_preserved_and_global_enum_names_are_unambiguous() -> None:
    snapshot = canonical_snapshot(
        parse_catalog(
            PRODUCTS,
            {"285": {"data": [attribute(1, "יצרן")]}, "325": {"data": [attribute(2, "יצרן")]}},
            fetched_at=FETCHED,
        )
    )
    components, desktops = snapshot.products
    assert components.name == "COMPUTER_COMPONENTS"
    assert [option.id for option in components.attributes[0].options] == ["1"]
    assert [option.id for option in desktops.attributes[0].options] == ["2"]
    assert components.attributes[0].options[0].name != desktops.attributes[0].options[0].name
    assert len(merged_attributes(snapshot)[0].options) == 2
    assert canonical_snapshot(snapshot) == snapshot


def test_explicit_empty_attributes_are_valid_but_missing_responses_are_not() -> None:
    snapshot = parse_catalog(PRODUCTS, {"285": {"data": []}, "325": {"data": []}}, fetched_at=FETCHED)
    assert all(not product.attributes for product in snapshot.products)
    with pytest.raises(ParseError, match="complete"):
        parse_catalog(PRODUCTS, {"285": {"data": []}}, fetched_at=FETCHED)


@pytest.mark.parametrize("bad", [{}, {"data": None}, {"data": {}}, {"data": [{"id": 24}]}])
def test_attribute_schema_drift_is_not_an_empty_catalog(bad: JsonValue) -> None:
    with pytest.raises(ParseError):
        parse_catalog(PRODUCTS, {"285": bad, "325": {"data": []}}, fetched_at=FETCHED)


def test_conflicting_shared_option_ids_are_rejected() -> None:
    snapshot = parse_catalog(
        PRODUCTS,
        {"285": {"data": [attribute(1, "first")]}, "325": {"data": [attribute(1, "different")]}},
        fetched_at=FETCHED,
    )
    with pytest.raises(ValueError, match="Conflicting labels"):
        canonical_snapshot(snapshot)
