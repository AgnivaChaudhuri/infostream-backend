"""Backend logging configuration and redaction utilities.

This module provides:
- uniform structlog configuration for the backend process
- dual file sinks (JSON + human-readable) plus console output
- strict recursive redaction helpers for sensitive keys/values
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path
from typing import Any, cast

import structlog
from structlog.typing import EventDict, Processor

from infostream.core.config import BackendConfig
from infostream.core.paths import resolve_system_root

# Sensitive key patterns that must never be logged in clear text.
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "token",
    "password",
    "passwd",
    "secret",
    "cookie",
    "set_cookie",
    "access_key",
    "private_key",
)

# Simple value-level signature checks for accidental secret strings.
_SENSITIVE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*bearer\s+", re.IGNORECASE),
    re.compile(r"^\s*sk-[a-zA-Z0-9]{10,}"),
)

_REDACTED_VALUE = "***REDACTED***"
_MAX_LOG_STRING_LENGTH = 4000


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def sanitize_for_log(value: Any, *, key_hint: str | None = None) -> Any:
    """Recursively redact secret-like fields and truncate huge strings."""
    if key_hint is not None and _is_sensitive_key(key_hint):
        return _REDACTED_VALUE

    if isinstance(value, dict):
        return {
            str(key): sanitize_for_log(sub_value, key_hint=str(key))
            for key, sub_value in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_log(item, key_hint=key_hint) for item in value]

    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"

    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
            return _REDACTED_VALUE
        if len(value) > _MAX_LOG_STRING_LENGTH:
            return f"{value[:_MAX_LOG_STRING_LENGTH]}...[truncated]"
        return value

    return value


def _redaction_processor(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor that enforces redaction before rendering."""
    sanitized = sanitize_for_log(event_dict)
    if not isinstance(sanitized, dict):
        return {"event": str(sanitized)}
    return sanitized


def get_log_directory(config: BackendConfig) -> Path:
    """Resolve and create the backend log directory for this process."""
    root = resolve_system_root(config)
    logs_dir = root / config.logs_dirname
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a component-bound backend logger."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger().bind(component=component))


def _resolve_log_level(config: BackendConfig) -> int:
    configured = config.log_level.upper().strip()
    return getattr(logging, configured, logging.DEBUG)


def _build_file_handler(
    *,
    path: Path,
    formatter: logging.Formatter,
    config: BackendConfig,
) -> logging.Handler:
    """Create one rotating file handler using size-based rollover."""
    handler = logging.handlers.RotatingFileHandler(
        filename=path,
        maxBytes=config.log_rotation_max_bytes,
        backupCount=config.log_rotation_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    return handler


def configure_logging(config: BackendConfig) -> None:
    """Configure backend logging sinks and structlog processors.

    Outputs:
    - console (human-readable)
    - logs/backend.log (human-readable)
    - logs/backend.jsonl (structured JSON)
    """
    logs_dir = get_log_directory(config)
    log_level = _resolve_log_level(config)

    pre_chain: list[Processor] = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redaction_processor,
    ]

    human_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processor=structlog.dev.ConsoleRenderer(colors=False),
    )
    json_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processor=structlog.processors.JSONRenderer(sort_keys=True),
    )

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(human_formatter)

    human_file_handler = _build_file_handler(
        path=logs_dir / "backend.log",
        formatter=human_formatter,
        config=config,
    )
    json_file_handler = _build_file_handler(
        path=logs_dir / "backend.jsonl",
        formatter=json_formatter,
        config=config,
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(human_file_handler)
    root_logger.addHandler(json_file_handler)

    # Route uvicorn logger output through our configured handlers.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target_logger = logging.getLogger(logger_name)
        target_logger.handlers.clear()
        target_logger.propagate = True
        target_logger.setLevel(log_level)

    # Keep asyncio internals at INFO to avoid noisy selector debug traces.
    logging.getLogger("asyncio").setLevel(logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redaction_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    get_logger("core.logging").info(
        "logging_configured",
        message="backend logging configured",
        level_name=logging.getLevelName(log_level),
        logs_dir=str(logs_dir),
        rotation_max_bytes=config.log_rotation_max_bytes,
        rotation_backups=config.log_rotation_backup_count,
    )
