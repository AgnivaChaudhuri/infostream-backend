from __future__ import annotations

"""Hashing helpers for content hashes and stable item references."""

import hashlib
from urllib.parse import ParseResult, urlparse, urlunparse

from infostream.core.text_normalization import normalize_text_for_hashing

def sha256_hex(value: bytes) -> str:
    """Return lowercase SHA-256 hex digest."""
    return hashlib.sha256(value).hexdigest()


def normalize_newlines(text: str) -> str:
    """Backward-compatible newline normalization helper."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_hash(content_text: str | None) -> str:
    """Compute deterministic content hash after canonical text normalization."""
    normalized = normalize_text_for_hashing(content_text or "")
    return sha256_hex(normalized.encode("utf-8"))


def canonicalize_url(url: str) -> str:
    """Canonicalize URL for stable dedup/item_ref behavior."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    if ":" in netloc:
        host, port = netloc.rsplit(":", 1)
        if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
            netloc = host

    canonical = ParseResult(
        scheme=scheme,
        netloc=netloc,
        path=parsed.path or "/",
        params=parsed.params,
        query=parsed.query,
        fragment="",
    )
    return urlunparse(canonical)


def compute_item_ref(
    source_uid: str,
    external_id: str | None,
    url: str | None,
    content_hash_value: str,
) -> str:
    """Compute stable item_ref from precedence inputs, then hash payload."""
    if external_id:
        payload = f"{source_uid}:{external_id}"
    elif url:
        payload = f"{source_uid}:{canonicalize_url(url)}"
    else:
        payload = f"{source_uid}:{content_hash_value}"
    return f"item:{sha256_hex(payload.encode('utf-8'))}"
