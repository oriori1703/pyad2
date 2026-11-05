"""Serializable catalog snapshot, including product-specific attribute options."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from yad2_client.models import Model


class CatalogEntry(Model):
    id: str = Field(pattern=r"^[0-9]+$")
    name: str
    label_en: str
    label_he: str


class OptionDefinition(CatalogEntry):
    attribute_id: str = Field(pattern=r"^[0-9]+$")


class AttributeDefinition(CatalogEntry):
    input_type: str
    required: bool = False
    options: tuple[OptionDefinition, ...] = ()

    @model_validator(mode="after")
    def valid_options(self) -> Self:
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids) or any(option.attribute_id != self.id for option in self.options):
            raise ValueError("Duplicate or incorrectly scoped attribute options")
        return self


class ProductDefinition(CatalogEntry):
    attributes: tuple[AttributeDefinition, ...] = ()

    @model_validator(mode="after")
    def unique_attributes(self) -> Self:
        ids = [attribute.id for attribute in self.attributes]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate attribute IDs in product")
        return self


class CatalogSnapshot(Model):
    fetched_at: datetime
    source_url: str
    products: tuple[ProductDefinition, ...]

    @model_validator(mode="after")
    def unique_products(self) -> Self:
        if not self.products:
            raise ValueError("Refusing an empty catalog snapshot")
        ids = [product.id for product in self.products]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate product IDs in catalog")
        return self


class CatalogProducts(Model):
    fetched_at: datetime
    source_url: str
    products: tuple[CatalogEntry, ...]
