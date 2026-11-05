import json
from urllib.parse import parse_qs, urlsplit

import pytest

from yad2_client import Attribute, AttributeFilter, MarketSearch, ProductType
from yad2_client.catalog import catalog_product, catalog_products, load_catalog, resolve_product_type
from yad2_client.options import OPTION_ENUMS, ComputerComponentTypeOption
from yad2_client.urls import market_url


def test_entire_bundled_snapshot_round_trips_through_generated_enums() -> None:
    snapshot = load_catalog()
    assert len(ProductType) == len(snapshot.products)
    seen_attributes: set[str] = set()
    seen_options: dict[str, set[str]] = {}
    for product in snapshot.products:
        member = ProductType(product.id)
        assert member.name == product.name
        assert (member.label_en, member.label_he) == (product.label_en, product.label_he)
        for attribute in product.attributes:
            seen_attributes.add(attribute.id)
            assert Attribute(attribute.id).name == attribute.name
            for option in attribute.options:
                seen_options.setdefault(attribute.id, set()).add(option.id)
                enum = OPTION_ENUMS[Attribute(attribute.id)]
                assert enum(option.id).name == option.name
                assert enum(option.id).label_he == option.label_he
    assert {str(attribute) for attribute in Attribute} == seen_attributes
    for attribute_id, enum in OPTION_ENUMS.items():
        assert {str(option) for option in enum} == seen_options[str(attribute_id)]


def test_enum_filters_serialize_to_ids_and_url_parameters() -> None:
    search = MarketSearch(
        product_types=(ProductType.COMPUTER_COMPONENTS,),
        attributes=(
            AttributeFilter(
                attribute_id=Attribute.COMPUTER_COMPONENT_TYPE,
                values=(ComputerComponentTypeOption.כרטיס_מסך,),
            ),
        ),
    )
    assert json.loads(search.model_dump_json())["attributes"] == [{"attribute_id": "155", "values": ["13287"]}]
    assert parse_qs(urlsplit(market_url(search)).query)["productTypes"] == ["285"]
    assert parse_qs(urlsplit(market_url(search)).query)["attr_155"] == ["13287"]


def test_lookup_uses_names_labels_and_ids_and_preserves_raw_future_ids() -> None:
    components = catalog_product("285")
    for value in ("computer_components", "Computer Components", "רכיבי מחשב"):
        assert catalog_product(value) == components
    assert "285" in {product.id for product in catalog_products("computer").products}
    assert resolve_product_type("999999999") == "999999999"
    with pytest.raises(ValueError, match="Unknown"):
        catalog_product("not a product")
    with pytest.raises(ValueError):
        ProductType("999999999")
