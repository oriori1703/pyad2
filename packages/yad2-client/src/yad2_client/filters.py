"""Pure parsing of the observed recommerce-feed/filters response."""

from pydantic import BaseModel, Field, ValidationError

from yad2_client.errors import ParseError
from yad2_client.models import FilterOption, PageSnapshot, SearchAttribute, SearchFilters


class _Option(BaseModel):
    value: str = Field(pattern=r"^[0-9]+$")
    text: str
    count: int = Field(ge=0, strict=True)


class _Attribute(BaseModel):
    attributeId: int = Field(gt=0, strict=True)
    attributeTextHeb: str
    attributeTextEng: str
    options: list[_Option]
    totalOptionsCount: int = Field(ge=0, strict=True)


class _Price(BaseModel):
    min: float | None = Field(strict=True, ge=0, allow_inf_nan=False)
    max: float | None = Field(strict=True, ge=0, allow_inf_nan=False)


class _Filters(BaseModel):
    productTypes: list[_Option]
    conditions: list[_Option]
    cities: list[_Option]
    attributes: list[_Attribute]
    price: _Price


class _Response(BaseModel):
    data: _Filters


def _options(values: list[_Option], group: str, warnings: list[str]) -> tuple[FilterOption, ...]:
    if len({value.value for value in values}) != len(values):
        warnings.append(
            f"{group} contains repeated option IDs; upstream labels/counts may conflict. Rows are preserved without merging."
        )
    return tuple(FilterOption(id=value.value, label=value.text, count=value.count) for value in values)


def parse_filters(snapshot: PageSnapshot) -> SearchFilters:
    try:
        data = _Response.model_validate(snapshot.data).data
    except ValidationError as exc:
        raise ParseError("Unrecognized live filter payload; refusing to report unavailable filters as empty") from exc
    if len({attribute.attributeId for attribute in data.attributes}) != len(data.attributes):
        raise ParseError("Duplicate live filter attribute IDs")
    if any(
        len({option.value for option in attribute.options}) > attribute.totalOptionsCount
        for attribute in data.attributes
    ):
        raise ParseError("Live filter option counts contradict the returned options")
    if data.price.min is not None and data.price.max is not None and data.price.min > data.price.max:
        raise ParseError("Live filter price bounds are reversed")
    warnings: list[str] = []
    return SearchFilters(
        source_url=snapshot.url,
        fetched_at=snapshot.fetched_at,
        cached=snapshot.cached,
        product_types=_options(data.productTypes, "product_types", warnings),
        conditions=_options(data.conditions, "conditions", warnings),
        cities=_options(data.cities, "cities", warnings),
        attributes=tuple(
            SearchAttribute(
                id=str(attribute.attributeId),
                label_en=attribute.attributeTextEng,
                label_he=attribute.attributeTextHeb,
                options=_options(attribute.options, f"attribute {attribute.attributeId}", warnings),
                total_options=attribute.totalOptionsCount,
                options_complete=len({option.value for option in attribute.options})
                == len(attribute.options)
                == attribute.totalOptionsCount,
            )
            for attribute in data.attributes
        ),
        min_price=data.price.min,
        max_price=data.price.max,
        warnings=tuple(warnings),
    )
