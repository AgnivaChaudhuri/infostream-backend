from __future__ import annotations

"""Unit tests for explicit route payload/query parsing helpers."""

import pytest

from infostream.api.routes import _parse_interests_payload, _parse_refs_query


def test_parse_interests_payload_accepts_valid_text() -> None:
    payload = b'{"interests":"ai, robotics"}'
    assert _parse_interests_payload(payload) == "ai, robotics"


def test_parse_interests_payload_rejects_invalid_shapes() -> None:
    bad_payloads = [
        b"not-json",
        b"[]",
        b"{}",
        b'{"interests":123}',
    ]
    for payload in bad_payloads:
        with pytest.raises(ValueError):
            _parse_interests_payload(payload)


def test_parse_refs_query_enforces_cap() -> None:
    refs = ",".join([f"item:{idx}" for idx in range(501)])
    with pytest.raises(ValueError):
        _parse_refs_query(refs, max_refs=500)


def test_parse_refs_query_trims_and_splits() -> None:
    refs = " item:a, item:b ,,"
    assert _parse_refs_query(refs, max_refs=500) == ["item:a", "item:b"]
