from __future__ import annotations

import logging
from pathlib import Path

from infostream.core.config import BackendConfig
from infostream.core.logging import configure_logging, get_logger, sanitize_for_log


def _flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        flush = getattr(handler, "flush", None)
        if callable(flush):
            flush()


def test_sanitize_for_log_redacts_sensitive_fields() -> None:
    payload = {
        "api_key": "sk-sensitive-key",
        "nested": {
            "authorization": "Bearer abc",
            "safe": "ok",
        },
    }

    sanitized = sanitize_for_log(payload)

    assert sanitized["api_key"] == "***REDACTED***"
    assert sanitized["nested"]["authorization"] == "***REDACTED***"
    assert sanitized["nested"]["safe"] == "ok"


def test_configure_logging_writes_human_and_json_files(tmp_path: Path) -> None:
    config = BackendConfig(home=tmp_path / "home")
    configure_logging(config)

    logger = get_logger("tests.logging")
    logger.info(
        "logging_smoke_event",
        message="logging smoke event",
        api_key="sk-123456789",
        detail="ok",
    )
    _flush_handlers()

    logs_dir = config.home / config.logs_dirname
    human_path = logs_dir / "backend.log"
    json_path = logs_dir / "backend.jsonl"

    assert human_path.exists()
    assert json_path.exists()

    human_body = human_path.read_text(encoding="utf-8")
    json_body = json_path.read_text(encoding="utf-8")

    assert "logging_smoke_event" in human_body
    assert "logging_smoke_event" in json_body
    assert "***REDACTED***" in human_body
    assert "***REDACTED***" in json_body
    assert "sk-123456789" not in human_body
    assert "sk-123456789" not in json_body
