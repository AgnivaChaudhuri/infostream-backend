from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from infostream.core.config import BackendConfig
from infostream.main import create_app


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def demo_source_path(repo_root: Path) -> Path:
    return repo_root / "examples" / "sources" / "demo_source"


@pytest.fixture
def test_home(tmp_path: Path) -> Path:
    return tmp_path / "infostream-home"


@pytest.fixture
def client(test_home: Path) -> TestClient:
    config = BackendConfig(home=test_home)
    app = create_app(config=config, port=19099)
    with TestClient(app) as api_client:
        yield api_client
