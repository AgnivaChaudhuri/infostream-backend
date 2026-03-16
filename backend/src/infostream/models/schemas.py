from __future__ import annotations

"""Pydantic models shared across API, persistence, and digest layers."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

class ApiEnvelope(BaseModel):
    """Uniform API envelope used by all endpoints."""

    ok: bool
    message: str
    error_code: str | None = None
    data: Any | None = None


class LlmConfig(BaseModel):
    """Provider configuration for digest summarization."""

    name: str = ""
    mode: Literal["local", "cloud"] = "local"
    unique_id: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class SettingsRecord(BaseModel):
    interests: str = ""
    llm: LlmConfig = Field(default_factory=LlmConfig)
    schedule: str = "9999-12-31T23:59:59Z"
    run_every: bool = False
    spacing: int = 21600


class SourceRecord(BaseModel):
    uid: str
    name: str
    developer: str
    version: str
    description: str
    platforms: list[str]
    enabled: bool = False
    exec_path: str
    interface_version: str


class InstalledSourcesRecord(BaseModel):
    sources: list[SourceRecord] = Field(default_factory=list)


class SourceInfo(BaseModel):
    name: str = Field(alias="Name")
    description: str = Field(alias="Description")
    version: str = Field(alias="Version")
    interface_version: str = Field(alias="Interface version")
    developer: str = Field(default="unknown", alias="Developer")
    platforms: list[str] = Field(default_factory=list, alias="Platform")

    model_config = ConfigDict(populate_by_name=True)


class IncomingItem(BaseModel):
    source_uid: str | None = None
    external_id: str | None = None
    url: str | None = None
    title: str | None = None
    created_at: str | None = None
    content_text: str | None = None
    media_files: list[str] = Field(default_factory=list)
    media_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SourceOutput(BaseModel):
    items: list[IncomingItem] = Field(default_factory=list)
    next_cursor: str | None = None


class ItemRecord(BaseModel):
    item_ref: str
    source_uid: str
    external_id: str | None = None
    url: str | None = None
    title: str | None = None
    created_at: str | None = None
    ingested_at: str
    content_text: str | None = None
    content_hash: str
    media_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


RunStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


class RunRecord(BaseModel):
    run_id: str
    trigger: Literal["manual", "scheduled"]
    status: RunStatus
    started_at: str
    finished_at: str | None = None
    digest_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw_llm_output: str | None = None


class RunSourceResult(BaseModel):
    source_uid: str
    status: Literal["succeeded", "failed", "skipped"]
    duration_ms: int
    items_ingested_count: int
    stderr_tail: str


class DigestRecord(BaseModel):
    digest_id: str
    run_id: str
    version: str
    created_at: str
    output_json: dict[str, Any]


class ScheduleRequest(BaseModel):
    schedule: str
    run_every: bool
    spacing: int


class InterestsRequest(BaseModel):
    interests: str


class LlmRequest(BaseModel):
    name: str
    mode: Literal["local", "cloud"]
    unique_id: str
    config: dict[str, Any] = Field(default_factory=dict)


class InstallSourceRequest(BaseModel):
    path: str


class LockfileRecord(BaseModel):
    api_version: str
    port: int
    pid: int
    started_at: str


class DigestEvent(BaseModel):
    title: str
    start: str
    end: str
    tz: str
    location: str
    description: str


class InterestBullet(BaseModel):
    text: str
    source_refs: list[str]
    confidence: Literal["low", "medium", "high"]


class DigestOutput(BaseModel):
    version: str
    new_communications: dict[str, str]
    important_dates_events: list[DigestEvent]
    interests: dict[str, list[InterestBullet]]
    links: list[str]
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    extraneous: str


def now_rfc3339() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
