"""Scheduler loop that converts saved schedule settings into digest runs.

This module intentionally keeps scheduling deterministic and explicit through a
small in-memory state machine:
- `stabilizing`: startup grace period before first scheduling decision.
- `ready`: steady-state scheduling tick loop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from infostream.core.config import BackendConfig
from infostream.core.logging import get_logger
from infostream.core.state import StateStore
from infostream.core.validation import parse_rfc3339
from infostream.digest.service import DigestBusyError, DigestRunService


class SchedulerService:
    """Periodic scheduler that triggers digest runs when due."""

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
        self._phase: Literal["stabilizing", "ready"] = "stabilizing"
        self._last_triggered_schedule: str | None = None
        self._last_tick_at: datetime | None = None
        self._logger = get_logger("scheduler.service")

    def is_running(self) -> bool:
        """Return True when scheduler loop task exists and is active."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start scheduler loop if not already running."""
        if self.is_running():
            self._logger.debug(
                "scheduler_start_skipped", message="scheduler start skipped already running"
            )
            return
        self._stopped.clear()
        self._phase = "stabilizing"
        self._task = asyncio.create_task(self._loop())
        self._logger.info("scheduler_started", message="scheduler started", phase=self._phase)

    async def stop(self) -> None:
        """Stop scheduler loop and wait for cancellation."""
        if self._task is None:
            self._logger.debug(
                "scheduler_stop_skipped", message="scheduler stop skipped no active task"
            )
            return
        self._stopped.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._phase = "stabilizing"
        self._logger.info("scheduler_stopped", message="scheduler stopped")

    async def _loop(self) -> None:
        """Apply startup stabilization delay, then run periodic ticks."""
        self._logger.debug(
            "scheduler_stabilizing",
            message="scheduler entering stabilization phase",
            stabilization_seconds=self._config.startup_stabilization_seconds,
        )
        await asyncio.sleep(self._config.startup_stabilization_seconds)
        self._phase = "ready"
        self._logger.info("scheduler_ready", message="scheduler ready")
        while not self._stopped.is_set():
            try:
                await self._tick()
            except Exception:
                # Scheduler should continue even if one tick fails.
                self._logger.exception("scheduler_tick_failed", message="scheduler tick failed")
            await asyncio.sleep(self._config.scheduler_tick_seconds)

    async def _tick(self) -> None:
        """Evaluate current schedule and launch digest run if due."""
        now = datetime.now(tz=UTC)
        self._logger.debug(
            "scheduler_tick_started", message="scheduler tick started", now=now.isoformat()
        )
        self._handle_clock_regression(now)
        settings = self._state_store.read_settings()
        schedule_value = settings.schedule
        if schedule_value == self._config.one_shot_schedule_sentinel:
            self._last_tick_at = now
            self._logger.debug(
                "scheduler_tick_skipped", message="scheduler tick skipped sentinel schedule"
            )
            return

        try:
            next_run = parse_rfc3339(schedule_value)
        except ValueError:
            self._last_tick_at = now
            self._logger.warning(
                "scheduler_schedule_invalid",
                message="scheduler ignored invalid schedule",
                schedule=schedule_value,
            )
            return

        due = next_run.astimezone(UTC) <= now
        if not due:
            self._last_tick_at = now
            self._logger.debug(
                "scheduler_tick_not_due",
                message="scheduler tick skipped not due",
                next_run=next_run.isoformat(),
            )
            return

        # Prevent duplicate launches for one-shot schedules when the stored value doesn't move.
        if not settings.run_every and self._last_triggered_schedule == schedule_value:
            self._last_tick_at = now
            self._logger.debug(
                "scheduler_tick_duplicate_skipped",
                message="scheduler skipped duplicate one-shot trigger",
            )
            return

        try:
            await self._digest_service.launch_run("scheduled")
            self._last_triggered_schedule = schedule_value
            self._logger.info(
                "scheduler_run_launched",
                message="scheduler launched digest run",
                schedule=schedule_value,
            )
        except DigestBusyError:
            self._last_tick_at = now
            self._logger.debug("scheduler_run_busy", message="scheduler launch skipped digest busy")
            return
        self._last_tick_at = now

    def _handle_clock_regression(self, now: datetime) -> None:
        """Reset one-shot dedupe marker when wall clock moves backwards.

        This avoids stale dedupe state after manual clock changes and keeps
        scheduler behavior predictable across local time adjustments.
        """
        if self._last_tick_at is None:
            return
        if now < self._last_tick_at:
            self._last_triggered_schedule = None
            self._logger.warning(
                "scheduler_clock_regression",
                message="scheduler detected clock regression",
                previous_tick=self._last_tick_at.isoformat(),
                now=now.isoformat(),
            )
