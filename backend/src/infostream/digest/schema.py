from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator

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
    errors = [error.message for error in _validator.iter_errors(payload)]
    return errors


def validate_citations(payload: dict[str, Any], valid_item_refs: set[str]) -> list[str]:
    errors: list[str] = []

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
                    errors.append(f"interest '{interest_name}' bullet {index} has invalid source_refs")
                    continue
                for ref in refs:
                    if ref not in valid_item_refs:
                        errors.append(
                            f"interest '{interest_name}' bullet {index} contains unknown ref {ref}"
                        )

    for field in ("entities", "relations"):
        for index, item in enumerate(_as_list(payload.get(field))):
            if not isinstance(item, dict):
                continue
            refs = item.get("source_refs")
            if refs is None:
                continue
            if not isinstance(refs, list):
                errors.append(f"{field}[{index}] has invalid source_refs")
                continue
            for ref in refs:
                if ref not in valid_item_refs:
                    errors.append(f"{field}[{index}] contains unknown ref {ref}")

    return errors


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []
