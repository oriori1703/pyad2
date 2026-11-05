"""Normalize the observed publishing metadata without retaining account data."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from yad2_client.catalog_codegen import enum_name
from yad2_client.catalog_models import AttributeDefinition, CatalogSnapshot, OptionDefinition, ProductDefinition
from yad2_client.errors import ParseError

CATALOG_URL = "https://gw.yad2.co.il/upsert-ad/recommerce/categories"


class _Product(BaseModel):
    id: int = Field(gt=0)
    heName: str = Field(min_length=1)
    enName: str = Field(min_length=1)


class _Option(BaseModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1)


class _Attribute(BaseModel):
    id: int = Field(gt=0)
    heName: str = Field(min_length=1)
    enName: str = Field(min_length=1)
    inputType: str = Field(min_length=1)
    required: bool
    values: tuple[_Option, ...]


class _Products(BaseModel):
    model_config = ConfigDict(strict=True)
    data: list[_Product] = Field(min_length=1)


class _Attributes(BaseModel):
    model_config = ConfigDict(strict=True)
    data: list[_Attribute]


def product_ids(payload: JsonValue) -> tuple[str, ...]:
    try:
        products = _Products.model_validate(payload)
        ids = tuple(str(product.id) for product in products.data)
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate product IDs")
        return ids
    except (ValidationError, ValueError) as exc:
        raise ParseError("Unrecognized or empty product catalog") from exc


def parse_catalog(
    products_payload: JsonValue,
    attribute_payloads: dict[str, JsonValue],
    *,
    fetched_at: datetime,
) -> CatalogSnapshot:
    """Require attribute responses for every product; never silently generate a partial snapshot."""
    ids = product_ids(products_payload)
    if set(ids) != set(attribute_payloads):
        raise ParseError("Attribute responses must cover exactly the complete product catalog")
    try:
        products = _Products.model_validate(products_payload)
        normalized: list[ProductDefinition] = []
        for product in products.data:
            attributes = _Attributes.model_validate(attribute_payloads[str(product.id)])
            normalized.append(
                ProductDefinition(
                    id=str(product.id),
                    name=enum_name(product.enName, str(product.id)),
                    label_en=product.enName,
                    label_he=product.heName,
                    attributes=tuple(
                        AttributeDefinition(
                            id=str(attribute.id),
                            name=enum_name(attribute.enName, str(attribute.id)),
                            label_en=attribute.enName,
                            label_he=attribute.heName,
                            input_type=attribute.inputType,
                            required=attribute.required,
                            options=tuple(
                                OptionDefinition(
                                    id=str(option.id),
                                    name=enum_name(option.name, str(option.id)),
                                    label_en="",
                                    label_he=option.name,
                                    attribute_id=str(attribute.id),
                                )
                                for option in sorted(attribute.values, key=lambda option: option.id)
                            ),
                        )
                        for attribute in sorted(attributes.data, key=lambda attribute: attribute.id)
                    ),
                )
            )
        return CatalogSnapshot(
            fetched_at=fetched_at,
            source_url=CATALOG_URL,
            products=tuple(sorted(normalized, key=lambda product: int(product.id))),
        )
    except ValidationError as exc:
        raise ParseError("Unrecognized catalog attribute metadata") from exc
