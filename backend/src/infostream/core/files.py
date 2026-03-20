"""Filesystem helpers for JSON state files and daemon system layout."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from infostream.core.config import BackendConfig
from infostream.core.paths import BackendPaths


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically to avoid partial state on interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=str(path.parent),
        encoding="utf-8",
    ) as tmp:
        json.dump(payload, tmp, indent=2, ensure_ascii=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file into native Python structures."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def ensure_system_layout(paths: BackendPaths, config: BackendConfig) -> None:
    """Create daemon root layout and initialize default state files."""
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.media_dir.mkdir(parents=True, exist_ok=True)
    paths.sources_dir.mkdir(parents=True, exist_ok=True)

    if not paths.installed_sources.exists():
        atomic_write_json(paths.installed_sources, {"sources": []})

    # Default LLM is intentionally unconfigured; user must opt in with PUT /llm/current.
    if not paths.settings.exists():
        atomic_write_json(
            paths.settings,
            {
                "interests": "",
                "llm": {
                    "name": "",
                    "mode": "local",
                    "unique_id": "",
                    "config": {},
                },
                "schedule": config.one_shot_schedule_sentinel,
                "run_every": False,
                "spacing": 21600,
            },
        )
