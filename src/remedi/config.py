from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    remedi_mode: Literal["fixture", "live"] = "fixture"
    remedi_api_key: str = ""
    datahub_gms_url: str = "http://localhost:8080"
    datahub_token: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    remedi_ops_webhook_url: str = ""
    remedi_ops_webhook_kind: Literal["generic", "slack"] = "generic"
    host: str = "0.0.0.0"
    port: int = 8790
    artifacts_dir: Path = Path("examples/generated")
    fixtures_dir: Path = Path("examples/fixtures")


@lru_cache
def get_settings() -> Settings:
    return Settings()
