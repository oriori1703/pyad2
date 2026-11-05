"""Validated public contracts. Money is in ILS; missing values remain null."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SortOrder(StrEnum):
    RELEVANCE = "relevance"
    PRICE_ASC = "priceAsc"
    PRICE_DESC = "priceDesc"
    NEWEST = "newest"
    OLDEST = "oldest"


class AttributeFilter(Model):
    """One observed attr_<id> option filter; enum members work as string IDs."""

    attribute_id: str = Field(pattern=r"^[0-9]+$")
    values: tuple[Annotated[str, Field(pattern=r"^[0-9]+$")], ...] = Field(min_length=1)


class MarketSearch(Model):
    query: str = Field(default="", max_length=500)
    collection: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    cities: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    product_types: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    attributes: tuple[AttributeFilter, ...] = ()
    sort: SortOrder = SortOrder.RELEVANCE
    page: int = Field(default=1, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_filters(self) -> Self:
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price must not exceed max_price")
        for values in (self.cities, self.areas, self.product_types, self.conditions):
            if any(not value.isascii() or not value.isdigit() for value in values):
                raise ValueError("Filter IDs must be numeric strings obtained from Yad2")
        if len({attribute.attribute_id for attribute in self.attributes}) != len(self.attributes):
            raise ValueError("Combine options for the same attribute into one filter")
        return self


class ListingAttribute(Model):
    attribute_id: str | None = None
    name: str | None = None
    label_en: str | None = None
    label_he: str | None = None
    option_id: str | None = None
    raw_value: JsonValue = None
    numeric_value: float | None = None
    unit: Literal["cm", "GB", "MHz", "kg"] | None = None
    id_source: Literal["listing", "catalog"] | None = None


class Listing(Model):
    id: str
    url: str
    section: str
    title: str
    price: float | None = None
    currency: str = "ILS"
    description: str | None = None
    city: str | None = None
    city_id: str | None = None
    area: str | None = None
    condition: str | None = None
    product_type: str | None = None
    product_type_id: str | None = None
    images: tuple[str, ...] = ()
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    normalized_attributes: tuple[ListingAttribute, ...] = ()
    seller_name: str | None = None


class Link(Model):
    title: str
    url: str


class Category(Link):
    slug: str


class PageSnapshot(Model):
    """Transport boundary; also usable for offline parsers and custom transports."""

    url: str
    fetched_at: datetime
    data: dict[str, JsonValue]
    links: tuple[Link, ...] = ()
    cached: bool = False


class SearchResult(Model):
    listings: tuple[Listing, ...]
    source_url: str
    fetched_at: datetime
    cached: bool = False
    total: int | None = None
    page: int = 1
    total_pages: int | None = None
    next_url: str | None = None
    warnings: tuple[str, ...] = ()


class ListingResult(Model):
    listing: Listing
    source_url: str
    fetched_at: datetime
    cached: bool = False


class CategoryResult(Model):
    categories: tuple[Category, ...]
    source_url: str
    fetched_at: datetime
    cached: bool = False


class FilterOption(Model):
    id: str
    label: str
    count: int = Field(ge=0)


class SearchAttribute(Model):
    id: str
    label_en: str
    label_he: str
    options: tuple[FilterOption, ...]
    total_options: int = Field(ge=0)
    options_complete: bool


class SearchFilters(Model):
    source_url: str
    fetched_at: datetime
    cached: bool = False
    product_types: tuple[FilterOption, ...]
    conditions: tuple[FilterOption, ...]
    cities: tuple[FilterOption, ...]
    attributes: tuple[SearchAttribute, ...]
    min_price: float | None = None
    max_price: float | None = None


class SearchPage(Model):
    source_url: str
    page: int
    fetched_at: datetime
    cached: bool
    total: int | None
    total_pages: int | None
    warnings: tuple[str, ...] = ()


class SearchBatch(Model):
    listings: tuple[Listing, ...]
    pages: tuple[SearchPage, ...]
    next_url: str | None
    stop_reason: Literal["exhausted", "page_limit", "item_limit", "pagination_unknown"]
    complete: bool
    duplicates_removed: int
    warnings: tuple[str, ...] = ()
