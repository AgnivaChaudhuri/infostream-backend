from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from infostream.core.config import BackendConfig
from infostream.core.paths import BackendPaths

# This file ensures that system files are correct and creates them if necessary
# atomic_write_json and read_json are used in other files also

def atomic_write_json(path: Path, payload: Any) -> None: # Write JSON to file
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(payload, tmp, indent=2, ensure_ascii=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def read_json(path: Path) -> Any: # Read a JSON file passed as argument
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# Ensure system files and layout is correct, paths is the BackendPaths from paths.py 
# containing system data and config is the same for config.py
def ensure_system_layout(paths: BackendPaths, config: BackendConfig) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.media_dir.mkdir(parents=True, exist_ok=True)
    paths.sources_dir.mkdir(parents=True, exist_ok=True)

    if not paths.installed_sources.exists(): # Create installed_sources.json if it doesn't exist
        atomic_write_json(paths.installed_sources, {"sources": []})

    # Create default settings if settings.json doesn't exist
    if not paths.settings.exists():
        atomic_write_json(
            paths.settings,
            {
                "interests": "",
                "llm": {
                    "name": "stub-local",
                    "mode": "local",
                    "unique_id": "stub-local",
                    "config": {},
                },
                "schedule": config.one_shot_schedule_sentinel,
                "run_every": False,
                "spacing": 21600,
            },
        )
