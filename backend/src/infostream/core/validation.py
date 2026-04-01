"""Validation helpers for SemVer and RFC3339 schedule values."""

from __future__ import annotations

import re
from datetime import datetime
from typing import cast

from dateutil import parser  # type: ignore[import-untyped]

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def is_semver(value: str) -> bool:
    """Return True when `value` matches semantic-version syntax."""
    return bool(SEMVER_RE.match(value))


def parse_rfc3339(value: str) -> datetime:
    """Parse RFC3339 timestamp and reject values without timezone."""
    dt = cast(datetime, parser.isoparse(value))
    if dt.tzinfo is None:
        raise ValueError("schedule must include timezone")
    return dt
