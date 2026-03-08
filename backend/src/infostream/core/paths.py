from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

from infostream.core.config import BackendConfig


@dataclass(frozen=True)
class BackendPaths:
    root: Path
    lockfile: Path
    settings: Path
    installed_sources: Path
    db: Path
    media_dir: Path
    sources_dir: Path


def resolve_system_root(config: BackendConfig) -> Path:
    if config.home is not None:
        return config.home.expanduser().resolve()

    env_home = os.getenv("INFOSTREAM_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    home = Path.home()
    system = platform.system().lower()
    if system == "linux":
        return (home / ".config" / "infostream").resolve()
    if system == "darwin":
        return (home / "Library" / "Application Support" / "Infostream").resolve()
    if system == "windows":
        appdata = os.getenv("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return (base / "Infostream").resolve()
    return (home / ".infostream").resolve()


def build_paths(config: BackendConfig) -> BackendPaths:
    root = resolve_system_root(config)
    return BackendPaths(
        root=root,
        lockfile=root / config.lockfile_name,
        settings=root / config.settings_filename,
        installed_sources=root / config.installed_sources_filename,
        db=root / config.db_filename,
        media_dir=root / config.media_dirname,
        sources_dir=root / config.sources_dirname,
    )
