from __future__ import annotations

"""Summarizer provider interfaces and provider selection implementations.

The digest service relies on this module to keep provider-specific logic out of
run orchestration. Current implementations:
- deterministic stub provider for local/test usage
- OpenAI-compatible chat-completions provider for prototype cloud usage
"""

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx

from infostream.models.schemas import LlmConfig
from infostream.models.schemas import ItemRecord

class SummarizerProvider(ABC):
    """Abstract provider contract for summarize/repair operations."""

    @abstractmethod
    def summarize(self, items: list[ItemRecord], interests_text: str, llm: LlmConfig) -> str:
        raise NotImplementedError

    @abstractmethod
    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
    ) -> str:
        raise NotImplementedError


class StubSummarizerProvider(SummarizerProvider):
    """Deterministic provider used for v1 while real model integrations are out of scope."""

    def summarize(self, items: list[ItemRecord], interests_text: str, llm: LlmConfig) -> str:
        _ = llm
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
        llm: LlmConfig,
    ) -> str:
        _ = invalid_output
        _ = errors
        return self.summarize(items, interests_text, llm)

    def _parse_interests(self, interests_text: str) -> list[str]:
        tokens = [token.strip() for token in interests_text.replace("\n", ",").split(",")]
        clean = [token for token in tokens if token]
        if clean:
            return clean
        return ["general"]


class OpenAICompatibleProvider(SummarizerProvider):
    """OpenAI-compatible provider using the /chat/completions API shape."""

    def summarize(self, items: list[ItemRecord], interests_text: str, llm: LlmConfig) -> str:
        payload = self._call_chat_completions(
            llm=llm,
            prompt=self._build_prompt(items, interests_text),
        )
        return payload

    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
    ) -> str:
        repair_prompt = self._build_repair_prompt(invalid_output, errors, items, interests_text)
        return self._call_chat_completions(llm=llm, prompt=repair_prompt)

    def _call_chat_completions(self, llm: LlmConfig, prompt: str) -> str:
        """Submit chat completion request and return assistant text content."""
        base_url = str(llm.config.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        api_key = llm.config.get("api_key")
        model = str(llm.config.get("model", llm.unique_id))
        timeout_seconds = float(llm.config.get("timeout_seconds", 30))
        organization = llm.config.get("organization")
        if not api_key:
            raise RuntimeError("OpenAI-compatible provider requires config.api_key")
        if not model:
            raise RuntimeError("OpenAI-compatible provider requires a model")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if isinstance(organization, str) and organization:
            headers["OpenAI-Organization"] = organization

        body = {
            "model": model,
            "temperature": llm.config.get("temperature", 0.1),
            "messages": [{"role": "user", "content": prompt}],
            # Request strict JSON output while preserving provider portability.
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenAI-compatible provider returned empty content")
        return content

    def _build_prompt(self, items: list[ItemRecord], interests_text: str) -> str:
        """Build summarize prompt using canonical item data and user interests."""
        serializable_items = [item.model_dump() for item in items]
        payload = json.dumps(serializable_items, ensure_ascii=True)
        return (
            "You are an assistant that outputs JSON only.\n"
            "Given canonical items and user interests, return a digest JSON object with keys:\n"
            "version, new_communications, important_dates_events, interests, links, entities, relations, extraneous.\n"
            "All citations must be item_ref values from the provided items.\n"
            f"Interests: {interests_text}\n"
            f"Items: {payload}\n"
        )

    def _build_repair_prompt(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
    ) -> str:
        """Build repair prompt from validation errors and previous invalid output."""
        error_text = "; ".join(errors)
        return (
            f"{self._build_prompt(items, interests_text)}\n"
            "Your previous output failed validation.\n"
            f"Validation errors: {error_text}\n"
            f"Previous output: {invalid_output}\n"
            "Return corrected JSON only."
        )


class ProviderRouter(SummarizerProvider):
    """Provider-agnostic router that selects concrete provider from LLM config."""

    def __init__(
        self,
        *,
        stub_provider: SummarizerProvider | None = None,
        openai_provider: SummarizerProvider | None = None,
    ) -> None:
        self._stub_provider = stub_provider or StubSummarizerProvider()
        self._openai_provider = openai_provider or OpenAICompatibleProvider()

    def summarize(self, items: list[ItemRecord], interests_text: str, llm: LlmConfig) -> str:
        provider = self._select_provider(llm)
        return provider.summarize(items, interests_text, llm)

    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
    ) -> str:
        provider = self._select_provider(llm)
        return provider.repair(invalid_output, errors, items, interests_text, llm)

    def _select_provider(self, llm: LlmConfig) -> SummarizerProvider:
        """Select provider in a way that can be expanded without API changes."""
        if not llm.unique_id:
            raise RuntimeError("LLM is not configured")

        if llm.unique_id in {"stub-local", "stub-cloud", "stub"} or llm.name.startswith("stub"):
            return self._stub_provider
        return self._openai_provider
