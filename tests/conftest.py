from datetime import UTC, datetime
from pathlib import Path

import pytest

from yad2_client import MarketSearch, Settings
from yad2_client.cache import Cache
from yad2_client.models import PageSnapshot
from yad2_client.urls import market_url


@pytest.fixture
def anyio_backend() -> str:
    """Patchright's browser transport requires the asyncio backend."""
    return "asyncio"


@pytest.fixture
def agent_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", min_interval_seconds=0)
    monkeypatch.setenv("YAD2_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("YAD2_CACHE_DIR", str(settings.cache_dir))
    return settings


@pytest.fixture
async def seeded_cache(agent_settings: Settings, anyio_backend: str) -> PageSnapshot:
    snapshot = PageSnapshot(
        url=market_url(MarketSearch(query="RTX")),
        fetched_at=datetime.now(UTC),
        data={
            "props": {
                "pageProps": {
                    "initialFeed": {
                        "items": [{"id": 123, "title": "כרטיס מסך RTX", "price": 500}],
                        "totalItems": 1,
                        "totalPages": 1,
                        "currentPage": 1,
                    }
                }
            }
        },
    )
    await Cache(agent_settings.cache_dir).put(snapshot.url, snapshot)
    await Cache(agent_settings.cache_dir).put(f"search:{snapshot.url}", snapshot)
    return snapshot
