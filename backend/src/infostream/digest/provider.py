from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from infostream.models.schemas import ItemRecord


# This file implements the LLM provider and summarization
# Right now it is a stub, model will be chosen and the stub will be expanded

class SummarizerProvider(ABC): # The base class for an LLM summarizer
    @abstractmethod # summarize the list of items and output appropriately
    def summarize(self, items: list[ItemRecord], interests_text: str) -> str:
        raise NotImplementedError

    @abstractmethod # If output is invalid (doesn't pass tests), attempt to repair it
    def repair(self, invalid_output: str, errors: list[str], items: list[ItemRecord], interests_text: str) -> str:
        raise NotImplementedError


class StubSummarizerProvider(SummarizerProvider): # The current stub (this will be replaced with another class)
    """Deterministic provider used for v1 while real model integrations are out of scope."""

    def summarize(self, items: list[ItemRecord], interests_text: str) -> str:
        interests = self._parse_interests(interests_text)
        refs = [item.item_ref for item in items[:3]]
        source_summary = "No source items available"
        if items:
            source_summary = "; ".join(
                filter(
                    None,
                    [
                        item.title or item.content_text or item.url or item.item_ref
                        for item in items[:3]
                    ],
                )
            )

        interest_output: dict[str, list[dict[str, Any]]] = {}
        for interest in interests:
            interest_output[interest] = [
                {
                    "text": f"Key updates for '{interest}': {source_summary[:200]}",
                    "source_refs": refs,
                    "confidence": "medium",
                }
            ]

        payload = {
            "version": "0.1.0",
            "new_communications": {},
            "important_dates_events": [],
            "interests": interest_output,
            "links": [],
            "entities": [],
            "relations": [],
            "extraneous": "",
        }
        return json.dumps(payload)

    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
    ) -> str:
        _ = invalid_output
        _ = errors
        return self.summarize(items, interests_text)

    def _parse_interests(self, interests_text: str) -> list[str]:
        tokens = [token.strip() for token in interests_text.replace("\n", ",").split(",")]
        clean = [token for token in tokens if token]
        if clean:
            return clean
        return ["general"]
