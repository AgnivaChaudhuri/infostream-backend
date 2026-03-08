from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from infostream.core.config import BackendConfig
from infostream.core.state import StateStore
from infostream.core.validation import parse_rfc3339
from infostream.digest.service import DigestBusyError, DigestRunService


class SchedulerService:
    def __init__(
        self,
        *,
        config: BackendConfig,
        state_store: StateStore,
        digest_service: DigestRunService,
    ) -> None:
        self._config = config
        self._state_store = state_store
        self._digest_service = digest_service

        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._last_triggered_schedule: str | None = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopped.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(self._config.startup_stabilization_seconds)
        while not self._stopped.is_set():
            try:
                await self._tick()
            except Exception:
                # Scheduler should continue even if one tick fails.
                pass
            await asyncio.sleep(self._config.scheduler_tick_seconds)

    async def _tick(self) -> None:
        settings = self._state_store.read_settings()
        schedule_value = settings.schedule
        if schedule_value == self._config.one_shot_schedule_sentinel:
            return

        try:
            next_run = parse_rfc3339(schedule_value)
        except ValueError:
            return

        now = datetime.now(tz=UTC)
        due = next_run.astimezone(UTC) <= now
        if not due:
            return

        # Prevent duplicate launches for one-shot schedules when the stored value doesn't move.
        if not settings.run_every and self._last_triggered_schedule == schedule_value:
            return

        try:
            await self._digest_service.launch_run("scheduled")
            self._last_triggered_schedule = schedule_value
        except DigestBusyError:
            return
