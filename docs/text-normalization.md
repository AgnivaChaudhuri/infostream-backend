# Text Normalization (Hashing)

This document records the current normalization pipeline used before computing
`content_hash` values.

Normalization is implemented in `backend/src/infostream/core/text_normalization.py`
and intentionally isolated so it can be edited or rolled back independently.

## Rules

1. Convert `\r\n` and `\r` to `\n`.
2. Trim trailing whitespace from each line.
3. Collapse 3 or more consecutive blank lines into 2 blank lines.
4. Trim leading and trailing document whitespace.

## Rationale

- Keep canonical hashes stable across platform newline differences.
- Reduce false hash divergence from editor-introduced trailing spaces.
- Preserve paragraph intent while avoiding extreme blank-line noise.

## Rollback Scope

To revert this behavior, revert only:

- `backend/src/infostream/core/text_normalization.py`
- `backend/src/infostream/core/hashing.py` (the call site)

No database schema or API contract changes depend on this module.
