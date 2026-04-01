"""Lockfile read/write utilities shared by daemon startup and desktop bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

from infostream.core.files import atomic_write_json, read_json
from infostream.core.logging import get_logger
from infostream.models.schemas import LockfileRecord

LOGGER = get_logger("core.lockfile")


def write_lockfile(path: Path, record: LockfileRecord) -> None:
    """Write daemon lockfile atomically so readers never see partial JSON."""
    atomic_write_json(path, record.model_dump())
    LOGGER.info(
        "lockfile_written",
        message="daemon lockfile written",
        lockfile=str(path),
        pid=record.pid,
        port=record.port,
    )


def read_lockfile(path: Path) -> LockfileRecord:
    """Read and validate the daemon lockfile payload."""
    data = read_json(path)
    return LockfileRecord.model_validate(data)


def remove_lockfile(path: Path) -> None:
    """Remove daemon lockfile if present."""
    if path.exists():
        path.unlink(missing_ok=True)
        LOGGER.info("lockfile_removed", message="daemon lockfile removed", lockfile=str(path))


def pid_running(pid: int) -> bool:
    """Best-effort process liveness check used for stale-lock detection."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
