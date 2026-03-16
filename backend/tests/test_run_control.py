from __future__ import annotations

"""Unit tests for run cancellation and event publication behavior."""

from pathlib import Path

import pytest

from infostream.core.config import BackendConfig
from infostream.core.events import EventBroker
from infostream.core.files import ensure_system_layout
from infostream.core.paths import build_paths
from infostream.core.state import StateStore
from infostream.db.database import Database
from infostream.digest.provider import StubSummarizerProvider
from infostream.digest.service import (
    DigestRunService,
    RunAlreadyFinishedError,
    RunNotFoundError,
)
from infostream.models.schemas import RunRecord, now_rfc3339
from infostream.sources.executor import SourceExecutor
from infostream.sources.manager import SourceManager


def _build_service(tmp_path: Path) -> tuple[DigestRunService, Database, EventBroker]:
    """Create a fully wired digest service backed by temporary filesystem state."""
    config = BackendConfig(home=tmp_path / "home")
    paths = build_paths(config)
    ensure_system_layout(paths, config)

    db = Database(paths.db)
    db.init_schema()
    state_store = StateStore(paths)
    source_manager = SourceManager(paths, config)
    source_executor = SourceExecutor(config=config, media_root=paths.media_dir)
    event_broker = EventBroker()
    service = DigestRunService(
        db=db,
        state_store=state_store,
        source_manager=source_manager,
        source_executor=source_executor,
        summarizer=StubSummarizerProvider(),
        config=config,
        event_broker=event_broker,
    )
    return service, db, event_broker


@pytest.mark.asyncio
async def test_cancel_run_not_found(tmp_path: Path) -> None:
    service, db, _ = _build_service(tmp_path)
    with pytest.raises(RunNotFoundError):
        await service.cancel_run("missing")
    db.close()


@pytest.mark.asyncio
async def test_cancel_run_rejects_terminal_status(tmp_path: Path) -> None:
    service, db, _ = _build_service(tmp_path)
    run_id = "run-terminal"
    db.create_run(
        RunRecord(
            run_id=run_id,
            trigger="manual",
            status="failed",
            started_at=now_rfc3339(),
            finished_at=now_rfc3339(),
            error_code="RUN_FAILED",
            error_message="x",
        )
    )

    with pytest.raises(RunAlreadyFinishedError):
        await service.cancel_run(run_id)
    db.close()


@pytest.mark.asyncio
async def test_cancel_run_marks_queued_run_as_canceled_and_publishes_event(tmp_path: Path) -> None:
    service, db, event_broker = _build_service(tmp_path)
    run_id = "run-queued"
    db.create_run(
        RunRecord(
            run_id=run_id,
            trigger="manual",
            status="queued",
            started_at=now_rfc3339(),
        )
    )

    queue = await event_broker.subscribe()
    await service.cancel_run(run_id)
    updated = db.get_run(run_id)
    assert updated is not None
    assert updated.status == "canceled"

    event = queue.get_nowait()
    assert event["type"] == "run_canceled"
    assert event["run_id"] == run_id
    await event_broker.unsubscribe(queue)
    db.close()
