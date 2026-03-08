from __future__ import annotations

import os
from pathlib import Path

from infostream.core.files import atomic_write_json, read_json
from infostream.models.schemas import LockfileRecord


def write_lockfile(path: Path, record: LockfileRecord) -> None:
    atomic_write_json(path, record.model_dump())


def read_lockfile(path: Path) -> LockfileRecord:
    data = read_json(path)
    return LockfileRecord.model_validate(data)


def remove_lockfile(path: Path) -> None:
    if path.exists():
        path.unlink(missing_ok=True)


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
