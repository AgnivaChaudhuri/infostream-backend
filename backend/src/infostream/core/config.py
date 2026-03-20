"""Central runtime configuration for daemon behavior and feature toggles."""

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
    logs_dirname: str = "logs"
    settings_filename: str = "settings.json"
    installed_sources_filename: str = "installed_sources.json"
    sources_dirname: str = "sources"
    media_dirname: str = "lt_media"
    source_interface_version: str = "1.0.0"

    startup_stabilization_seconds: int = 5
    scheduler_tick_seconds: int = 1

    source_json_max_bytes: int = 5 * 1024 * 1024
    source_media_max_bytes: int = 200 * 1024 * 1024
    media_cache_max_bytes: int = 1024 * 1024 * 1024
    items_lookup_max_refs: int = 500

    graceful_source_timeout_seconds: int = 300
    hard_source_timeout_seconds: int = 600

    one_shot_schedule_sentinel: str = "9999-12-31T23:59:59Z"
    unavailable_llm_ids: set[str] = Field(default_factory=set)

    # Logging defaults prioritize detailed debugging visibility.
    log_level: str = "DEBUG"
    log_rotation_max_bytes: int = 20 * 1024 * 1024
    log_rotation_backup_count: int = 5

    home: Path | None = None
