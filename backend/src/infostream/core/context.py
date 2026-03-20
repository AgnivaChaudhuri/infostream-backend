"""Application context assembly and lifecycle wiring for backend subsystems."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from infostream.core.config import BackendConfig
from infostream.core.events import EventBroker
from infostream.core.files import ensure_system_layout
from infostream.core.lockfile import remove_lockfile, write_lockfile
from infostream.core.logging import get_logger
from infostream.core.paths import BackendPaths, build_paths
from infostream.core.state import StateStore
from infostream.db.database import Database
from infostream.digest.provider import ProviderRouter
from infostream.digest.service import DigestRunService
from infostream.models.schemas import LockfileRecord, now_rfc3339
from infostream.scheduler.service import SchedulerService
from infostream.sources.executor import SourceExecutor
from infostream.sources.manager import SourceManager


@dataclass
class AppContext:
    """Container for all long-lived backend services and state handles."""

    config: BackendConfig
    paths: BackendPaths
    db: Database
    state_store: StateStore
    source_manager: SourceManager
    source_executor: SourceExecutor
    digest_service: DigestRunService
    scheduler: SchedulerService
    event_broker: EventBroker
    port: int
    logger: Any

    async def startup(self) -> None:
        app_logger = self.logger
        app_logger.info(
            "context_startup_layout",
            message="initializing system layout",
            root=str(self.paths.root),
        )
        ensure_system_layout(self.paths, self.config)
        app_logger.debug("context_startup_db_schema", message="initializing database schema")
        self.db.init_schema()
        app_logger.debug(
            "context_startup_lockfile_write",
            message="writing daemon lockfile",
            lockfile=str(self.paths.lockfile),
            port=self.port,
        )
        write_lockfile(
            self.paths.lockfile,
            LockfileRecord(
                api_version=self.config.api_version,
                port=self.port,
                pid=os.getpid(),
                started_at=now_rfc3339(),
            ),
        )
        app_logger.debug("context_startup_scheduler_start", message="starting scheduler")
        await self.scheduler.start()
        app_logger.info("context_startup_completed", message="context startup completed")

    async def shutdown(self) -> None:
        app_logger = self.logger
        app_logger.info("context_shutdown_started", message="context shutdown started")
        await self.scheduler.stop()
        app_logger.debug(
            "context_shutdown_lockfile_remove",
            message="removing lockfile during shutdown",
            lockfile=str(self.paths.lockfile),
        )
        remove_lockfile(self.paths.lockfile)
        self.db.close()
        app_logger.info("context_shutdown_completed", message="context shutdown completed")


def create_context(config: BackendConfig, port: int) -> AppContext:
    """Create and wire all backend services for one daemon process."""
    app_logger = get_logger("core.context")
    paths = build_paths(config)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    db = Database(paths.db)
    state_store = StateStore(paths)
    source_manager = SourceManager(paths, config)
    source_executor = SourceExecutor(config=config, media_root=paths.media_dir)
    event_broker = EventBroker()

    digest_service = DigestRunService(
        db=db,
        state_store=state_store,
        source_manager=source_manager,
        source_executor=source_executor,
        summarizer=ProviderRouter(),
        config=config,
        event_broker=event_broker,
    )
    scheduler = SchedulerService(
        config=config,
        state_store=state_store,
        digest_service=digest_service,
    )

    return AppContext(
        config=config,
        paths=paths,
        db=db,
        state_store=state_store,
        source_manager=source_manager,
        source_executor=source_executor,
        digest_service=digest_service,
        scheduler=scheduler,
        event_broker=event_broker,
        port=port,
        logger=app_logger,
    )
