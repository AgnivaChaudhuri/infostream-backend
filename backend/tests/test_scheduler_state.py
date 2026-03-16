"""Unit tests for scheduler state-machine and dedupe behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from infostream.core.config import BackendConfig
from infostream.models.schemas import SettingsRecord
from infostream.scheduler.service import SchedulerService


class _FakeStateStore:
    """State-store fake that returns mutable settings."""

    def __init__(self, settings: SettingsRecord) -> None:
        self.settings = settings

    def read_settings(self) -> SettingsRecord:
        return self.settings


class _FakeDigestService:
    """Digest-service fake that records launch requests."""

    def __init__(self) -> None:
        self.launches: list[str] = []

    async def launch_run(self, trigger: str) -> str:
        self.launches.append(trigger)
        return "run-id"


@pytest.mark.asyncio
async def test_scheduler_one_shot_dedupes_same_schedule() -> None:
    due_time = (datetime.now(tz=UTC) - timedelta(seconds=1)).replace(microsecond=0)
    settings = SettingsRecord(
        interests="",
        llm={"name": "stub", "mode": "local", "unique_id": "stub-local", "config": {}},
        schedule=due_time.isoformat().replace("+00:00", "Z"),
        run_every=False,
        spacing=3600,
    )
    state_store = _FakeStateStore(settings)
    digest_service = _FakeDigestService()
    scheduler = SchedulerService(
        config=BackendConfig(),
        state_store=state_store,  # type: ignore[arg-type]
        digest_service=digest_service,  # type: ignore[arg-type]
    )

    await scheduler._tick()
    await scheduler._tick()
    assert digest_service.launches == ["scheduled"]


@pytest.mark.asyncio
async def test_scheduler_clock_regression_resets_dedupe_marker() -> None:
    due_time = (datetime.now(tz=UTC) - timedelta(seconds=1)).replace(microsecond=0)
    settings = SettingsRecord(
        interests="",
        llm={"name": "stub", "mode": "local", "unique_id": "stub-local", "config": {}},
        schedule=due_time.isoformat().replace("+00:00", "Z"),
        run_every=False,
        spacing=3600,
    )
    state_store = _FakeStateStore(settings)
    digest_service = _FakeDigestService()
    scheduler = SchedulerService(
        config=BackendConfig(),
        state_store=state_store,  # type: ignore[arg-type]
        digest_service=digest_service,  # type: ignore[arg-type]
    )

    await scheduler._tick()
    # Simulate clock moving backwards.
    scheduler._last_tick_at = datetime.now(tz=UTC) + timedelta(seconds=10)
    scheduler._handle_clock_regression(datetime.now(tz=UTC))
    await scheduler._tick()

    assert digest_service.launches == ["scheduled", "scheduled"]
