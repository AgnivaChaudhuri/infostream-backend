"""Unit tests for source install-time compatibility checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from infostream.core.config import BackendConfig
from infostream.core.files import ensure_system_layout
from infostream.core.paths import build_paths
from infostream.sources.manager import SourceError, SourceManager


def _make_source_package(tmp_path: Path, interface_version: str) -> Path:
    """Create a minimal source package directory for install tests."""
    source_dir = tmp_path / f"source-{interface_version.replace('.', '-')}"
    source_dir.mkdir(parents=True)
    (source_dir / "src_exec").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (source_dir / "src_info.json").write_text(
        (
            "{"
            f"\"name\":\"Demo Source\","
            f"\"description\":\"Demo\","
            f"\"version\":\"1.0.0\","
            f"\"interface_version\":\"{interface_version}\","
            "\"developer\":\"tester\","
            "\"platforms\":[\"linux\"]"
            "}"
        ),
        encoding="utf-8",
    )
    return source_dir


def test_install_accepts_matching_interface_major(tmp_path: Path) -> None:
    config = BackendConfig(home=tmp_path / "home", source_interface_version="1.5.0")
    paths = build_paths(config)
    ensure_system_layout(paths, config)
    manager = SourceManager(paths, config)

    source_path = _make_source_package(tmp_path, "1.2.3")
    installed = manager.install(source_path)
    assert installed.interface_version == "1.2.3"


def test_install_rejects_mismatched_interface_major(tmp_path: Path) -> None:
    config = BackendConfig(home=tmp_path / "home", source_interface_version="2.0.0")
    paths = build_paths(config)
    ensure_system_layout(paths, config)
    manager = SourceManager(paths, config)

    source_path = _make_source_package(tmp_path, "1.2.3")
    with pytest.raises(SourceError) as exc:
        manager.install(source_path)

    assert exc.value.error_code == "INVALID_SOURCE_STRUCTURE"
