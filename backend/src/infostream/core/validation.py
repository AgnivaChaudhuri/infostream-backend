from __future__ import annotations

import re
from datetime import datetime

from dateutil import parser

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def is_semver(value: str) -> bool:
    return bool(SEMVER_RE.match(value))


def parse_rfc3339(value: str) -> datetime:
    dt = parser.isoparse(value)
    if dt.tzinfo is None:
        raise ValueError("schedule must include timezone")
    return dt
