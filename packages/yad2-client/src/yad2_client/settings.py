"""Configuration via constructor or YAD2_* environment variables."""

import os
from pathlib import Path

from platformdirs import user_cache_path, user_data_path
from pydantic import Field

from yad2_client.models import Model


class Settings(Model):
    data_dir: Path = Field(default_factory=lambda: user_data_path("yad2"))
    cache_dir: Path = Field(default_factory=lambda: user_cache_path("yad2"))
    headless: bool = True
    channel: str = "chromium"
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    min_interval_seconds: float = Field(default=3, ge=0)
    cache_ttl_seconds: float = Field(default=300, ge=0)
    cooldown_seconds: float = Field(default=300, ge=0)
    service_idle_seconds: float = Field(default=300, ge=1)
    service_start_timeout_seconds: float = Field(default=20, ge=1)
    browser_queue_timeout_seconds: float = Field(default=120, ge=1)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.model_validate(
            {
                name: os.environ[f"YAD2_{name.upper()}"]
                for name in cls.model_fields
                if f"YAD2_{name.upper()}" in os.environ
            }
        )
