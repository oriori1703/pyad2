"""Typed, browser-backed access to Yad2."""

from yad2_client.client import Yad2Client
from yad2_client.enums import Attribute, ProductType
from yad2_client.models import (
    AttributeFilter,
    CategoryResult,
    FilterOption,
    Listing,
    ListingAttribute,
    ListingResult,
    MarketSearch,
    SearchAttribute,
    SearchBatch,
    SearchFilters,
    SearchPage,
    SearchResult,
    SortOrder,
)
from yad2_client.settings import Settings

__all__ = [
    "Attribute",
    "AttributeFilter",
    "CategoryResult",
    "FilterOption",
    "Listing",
    "ListingAttribute",
    "ListingResult",
    "MarketSearch",
    "ProductType",
    "SearchAttribute",
    "SearchBatch",
    "SearchFilters",
    "SearchPage",
    "SearchResult",
    "Settings",
    "SortOrder",
    "Yad2Client",
]
