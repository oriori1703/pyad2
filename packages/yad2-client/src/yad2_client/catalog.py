"""Offline discovery of product types and their applicable attribute options."""

from functools import lru_cache
from importlib.resources import files

from yad2_client.catalog_codegen import named_entries
from yad2_client.catalog_models import CatalogEntry, CatalogProducts, CatalogSnapshot, ProductDefinition


@lru_cache(maxsize=1)
def load_catalog() -> CatalogSnapshot:
    return CatalogSnapshot.model_validate_json(files("yad2_client").joinpath("data/catalog.json").read_text("utf-8"))


def catalog_products(query: str = "") -> CatalogProducts:
    """Search IDs, enum names, and original English/Hebrew labels, without a browser."""
    snapshot = load_catalog()
    needle = query.strip().casefold()
    return CatalogProducts(
        fetched_at=snapshot.fetched_at,
        source_url=snapshot.source_url,
        products=tuple(
            CatalogEntry(id=product.id, name=product.name, label_en=product.label_en, label_he=product.label_he)
            for product in named_entries(snapshot.products)
            if any(
                needle in value.casefold() for value in (product.id, product.name, product.label_en, product.label_he)
            )
        ),
    )


def catalog_product(value: str) -> ProductDefinition:
    """Look up an exact ID, enum name, or bilingual label; reject ambiguous names."""
    needle = value.strip().casefold()
    products = load_catalog().products
    names = {entry.id: entry.name for entry in named_entries(products)}
    matches = [
        product
        for product in products
        if needle
        in {product.id, names[product.id].casefold(), product.label_en.casefold(), product.label_he.casefold()}
    ]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous product type: {value!r}. Use catalog_products() to discover IDs.")
    return matches[0].model_copy(update={"name": names[matches[0].id]})


def resolve_product_type(value: str) -> str:
    """Preserve raw numeric IDs for new server-side types not yet in the snapshot."""
    return value if value.isascii() and value.isdigit() else catalog_product(value).id
