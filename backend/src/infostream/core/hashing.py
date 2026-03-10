from __future__ import annotations

import hashlib
from urllib.parse import ParseResult, urlparse, urlunparse

# Contains hashing functions, URL and item canonicalization,
# and item_refs for bookkeeping

def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_hash(content_text: str | None) -> str:
    normalized = normalize_newlines(content_text or "")
    return sha256_hex(normalized.encode("utf-8"))


def canonicalize_url(url: str) -> str:
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
    if external_id:
        payload = f"{source_uid}:{external_id}"
    elif url:
        payload = f"{source_uid}:{canonicalize_url(url)}"
    else:
        payload = f"{source_uid}:{content_hash_value}"
    return f"item:{sha256_hex(payload.encode('utf-8'))}"
