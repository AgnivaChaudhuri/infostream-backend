from __future__ import annotations

"""Text normalization rules used for deterministic hashing.

This module is intentionally isolated so reviewers can change or roll back text
normalization behavior without touching unrelated hashing logic.
"""

import re

# Keep at most two consecutive blank lines after whitespace cleanup.
_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_text_for_hashing(text: str) -> str:
    """Normalize text into a canonical form for content hash calculation.

    Rules:
    1. Normalize all newline forms to LF.
    2. Trim trailing whitespace on each line.
    3. Collapse 3+ blank lines to at most 2.
    4. Trim leading/trailing whitespace in the full document.
    """
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    trimmed_line_ws = "\n".join(line.rstrip() for line in normalized_newlines.split("\n"))
    collapsed_blank_lines = _MULTI_BLANK_LINES_RE.sub("\n\n", trimmed_line_ws)
    return collapsed_blank_lines.strip()
