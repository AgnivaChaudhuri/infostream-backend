# Ambiguity Defaults (v1)

This project applies explicit defaults for unresolved PDF ambiguities:

- Hash algorithm: SHA-256 for `content_hash`, `item_ref`, media hashes.
- Canonicalization:
  - Content newline normalization to `\n` before hashing.
  - URL normalization: lowercase scheme/host, strip fragment, remove default ports.
- SQLite database filename: `infostream.db`.
- Media cache retention: persistent in `lt_media/`.
- Media naming: `<sha256><original_ext>`.
- `entities` and `relations`: kept as arrays of objects; citation refs are validated when `source_refs` is present.
- One-shot schedule behavior (`run_every=false`): after successful scheduled run, schedule is set to sentinel `9999-12-31T23:59:59Z`.
