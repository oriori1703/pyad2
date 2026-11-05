from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from yad2_client import Settings
from yad2_client.models import PageSnapshot


@pytest.fixture
def anyio_backend() -> str:
    """Patchright's browser transport requires the asyncio backend."""
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", min_interval_seconds=0)


@pytest.fixture
def market_item() -> dict[str, JsonValue]:
    # Minimal, anonymized shape observed on /market/item/15405193887804 on 2026-11-05.
    return {
        "id": 15405193887804,
        "title": "NVIDIA 5070 ti",
        "price": 3500,
        "description": "כרטיס מסך עם 16 גיגה",
        "address": {"city": {"id": "5000", "textHeb": "תל אביב יפו"}},
        "condition": {"id": "3", "textHeb": "משומש"},
        "productType": {"id": "285", "textHeb": "רכיבי מחשב"},
        "images": ["https://example.com/gpu.jpg"],
        "attributeKeys": [{"attributeTextEng": "manufacture", "optionText": "Nvidia"}],
        "customer": {"name": "Example seller", "phone": ""},
    }


@pytest.fixture
def snapshot(market_item: dict[str, JsonValue]) -> PageSnapshot:
    return PageSnapshot(
        url="https://www.yad2.co.il/market/search?q=RTX",
        fetched_at=datetime.now(UTC),
        data={
            "props": {
                "pageProps": {
                    "initialFeed": {"items": [market_item], "totalItems": 22, "totalPages": 2, "currentPage": 1}
                }
            }
        },
    )
