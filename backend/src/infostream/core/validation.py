from __future__ import annotations

import re
from datetime import datetime
from typing import cast

from dateutil import parser  # type: ignore[import-untyped]

# Ensure that strings are valid format, both semantic versions
# and date/time as RFC3339 string

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def is_semver(value: str) -> bool:
    return bool(SEMVER_RE.match(value))


def parse_rfc3339(value: str) -> datetime:
    dt = cast(datetime, parser.isoparse(value))
    if dt.tzinfo is None:
        raise ValueError("schedule must include timezone")
    return dt
