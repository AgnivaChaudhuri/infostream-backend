from __future__ import annotations

from infostream.digest.schema import validate_citations, validate_digest_schema


def test_digest_schema_validation_success() -> None:
    payload = {
        "version": "0.1.0",
        "new_communications": {},
        "important_dates_events": [],
        "interests": {
            "general": [
                {
                    "text": "hello",
                    "source_refs": ["item:1"],
                    "confidence": "medium",
                }
            ]
        },
        "links": [],
        "entities": [],
        "relations": [],
        "extraneous": "",
    }
    assert validate_digest_schema(payload) == []
    assert validate_citations(payload, {"item:1"}) == []


def test_digest_schema_rejects_unknown_citation() -> None:
    payload = {
        "version": "0.1.0",
        "new_communications": {},
        "important_dates_events": [],
        "interests": {
            "general": [
                {
                    "text": "hello",
                    "source_refs": ["item:missing"],
                    "confidence": "medium",
                }
            ]
        },
        "links": [],
        "entities": [],
        "relations": [],
        "extraneous": "",
    }
    errors = validate_citations(payload, {"item:exists"})
    assert errors


def test_digest_schema_rejects_non_semver_version() -> None:
    payload = {
        "version": "latest",
        "new_communications": {},
        "important_dates_events": [],
        "interests": {},
        "links": [],
        "entities": [],
        "relations": [],
        "extraneous": "",
    }
    errors = validate_digest_schema(payload)
    assert any("SemVer" in error for error in errors)


def test_entities_and_relations_require_source_refs() -> None:
    payload = {
        "version": "0.1.0",
        "new_communications": {},
        "important_dates_events": [],
        "interests": {},
        "links": [],
        "entities": [{"name": "x"}],
        "relations": [{"name": "r", "source_refs": []}],
        "extraneous": "",
    }
    errors = validate_citations(payload, {"item:exists"})
    assert any("entities[0] is missing source_refs" in error for error in errors)
    assert any("relations[0] has empty source_refs" in error for error in errors)
