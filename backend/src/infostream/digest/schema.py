"""Schema and provenance validation helpers for digest payloads.

This module owns structural validation (jsonschema) and citation validation rules
enforced after a model/provider returns output.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from infostream.core.validation import is_semver

# LLM digest JSON schema - how JSON output should be formatted

DIGEST_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "version",
        "new_communications",
        "important_dates_events",
        "interests",
        "links",
        "entities",
        "relations",
        "extraneous",
    ],
    "properties": {
        "version": {"type": "string"},
        "new_communications": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "important_dates_events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "start", "end", "tz", "location", "description"],
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "tz": {"type": "string"},
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "interests": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "source_refs", "confidence"],
                    "properties": {
                        "text": {"type": "string"},
                        "source_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "links": {
            "type": "array",
            "items": {"type": "string"},
        },
        "entities": {
            "type": "array",
            "items": {"type": "object"},
        },
        "relations": {
            "type": "array",
            "items": {"type": "object"},
        },
        "extraneous": {"type": "string"},
    },
    "additionalProperties": True,
}


_validator = Draft202012Validator(DIGEST_JSON_SCHEMA)


def validate_digest_schema(payload: dict[str, Any]) -> list[str]:
    """Validate payload shape and strict semver requirements."""
    errors = [error.message for error in _validator.iter_errors(payload)]
    version = payload.get("version")
    if not isinstance(version, str) or not is_semver(version):
        errors.append("version must be a SemVer string")
    return errors


# Ensure source_refs point to ingested run item_refs
def validate_citations(payload: dict[str, Any], valid_item_refs: set[str]) -> list[str]:
    """Validate all citations against item_refs produced by the current run.

    Entities and relations are strict: every object must include a non-empty
    source_refs array, and every ref must exist in valid_item_refs.
    """
    errors: list[str] = []

    # Validate citation refs in interest bullets.
    interests = payload.get("interests", {})
    if isinstance(interests, dict):
        for interest_name, bullets in interests.items():
            if not isinstance(bullets, list):
                continue
            for index, bullet in enumerate(bullets):
                if not isinstance(bullet, dict):
                    continue
                refs = bullet.get("source_refs")
                if not isinstance(refs, list):
                    errors.append(
                        f"interest '{interest_name}' bullet {index} has invalid source_refs"
                    )
                    continue
                for ref in refs:
                    if ref not in valid_item_refs:
                        errors.append(
                            f"interest '{interest_name}' bullet {index} contains unknown ref {ref}"
                        )

    # Validate entities/relations with strict provenance requirements.
    for field in ("entities", "relations"):
        for index, item in enumerate(_as_list(payload.get(field))):
            if not isinstance(item, dict):
                continue
            refs = item.get("source_refs")
            if refs is None:
                errors.append(f"{field}[{index}] is missing source_refs")
                continue
            if not isinstance(refs, list):
                errors.append(f"{field}[{index}] has invalid source_refs")
                continue
            if not refs:
                errors.append(f"{field}[{index}] has empty source_refs")
                continue
            for ref in refs:
                if ref not in valid_item_refs:
                    errors.append(f"{field}[{index}] contains unknown ref {ref}")

    return errors


def _as_list(value: Any) -> list[Any]:
    """Convert unknown value to list for tolerant traversal."""
    if isinstance(value, list):
        return value
    return []
