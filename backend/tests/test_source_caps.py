from __future__ import annotations

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
