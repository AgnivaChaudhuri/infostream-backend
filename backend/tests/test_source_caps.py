"""Unit tests for source output and media retention caps."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from infostream.core.config import BackendConfig
from infostream.sources.executor import SourceExecutionError, SourceExecutor


def test_source_json_size_cap(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "src_output.json").write_text("x" * (6 * 1024 * 1024), encoding="utf-8")

    config = BackendConfig(home=tmp_path)
    executor = SourceExecutor(config=config, media_root=tmp_path / "lt_media")

    with pytest.raises(SourceExecutionError):
        executor._validate_output_size(source_dir)


def test_media_cache_retention_limit_evicts_oldest_files(tmp_path: Path) -> None:
    media_root = tmp_path / "lt_media"
    media_root.mkdir(parents=True)
    old_file = media_root / "old.bin"
    new_file = media_root / "new.bin"
    old_file.write_bytes(b"a" * 700)
    time.sleep(0.01)
    new_file.write_bytes(b"b" * 700)

    config = BackendConfig(home=tmp_path, media_cache_max_bytes=1000)
    executor = SourceExecutor(config=config, media_root=media_root)
    executor._enforce_media_cache_limit()

    assert not old_file.exists()
    assert new_file.exists()
