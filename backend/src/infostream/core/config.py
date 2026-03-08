from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendConfig(BaseSettings):
    """Runtime configuration with env overrides for local development/tests."""

    model_config = SettingsConfigDict(env_prefix="INFOSTREAM_", extra="ignore")

    api_version: str = "v0"
    db_filename: str = "infostream.db"
    lockfile_name: str = "daemon.lock"
    settings_filename: str = "settings.json"
    installed_sources_filename: str = "installed_sources.json"
    sources_dirname: str = "sources"
    media_dirname: str = "lt_media"

    startup_stabilization_seconds: int = 5
    scheduler_tick_seconds: int = 1

    source_json_max_bytes: int = 5 * 1024 * 1024
    source_media_max_bytes: int = 200 * 1024 * 1024

    graceful_source_timeout_seconds: int = 300
    hard_source_timeout_seconds: int = 600

    one_shot_schedule_sentinel: str = "9999-12-31T23:59:59Z"
    unavailable_llm_ids: set[str] = Field(default_factory=set)

    home: Path | None = None
