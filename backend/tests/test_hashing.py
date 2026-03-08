from __future__ import annotations

from infostream.core.hashing import canonicalize_url, compute_item_ref, content_hash


def test_canonicalize_url_normalizes_host_port_and_fragment() -> None:
    url = "HTTPS://Example.COM:443/path?q=1#frag"
    assert canonicalize_url(url) == "https://example.com/path?q=1"


def test_content_hash_normalizes_newlines() -> None:
    assert content_hash("a\r\nb") == content_hash("a\nb")


def test_item_ref_precedence() -> None:
    by_external = compute_item_ref("src", "ext-1", "https://example.com/a", "h")
    by_url = compute_item_ref("src", None, "https://example.com/a", "h")
    by_hash = compute_item_ref("src", None, None, "h")

    assert by_external.startswith("item:")
    assert by_url.startswith("item:")
    assert by_hash.startswith("item:")
    assert by_external != by_url
    assert by_url != by_hash
