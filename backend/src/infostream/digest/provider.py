"""Summarizer provider interfaces and provider selection implementations.

The digest service relies on this module to keep provider-specific logic out of
run orchestration. Current implementations:
- deterministic stub provider for local/test usage
- OpenAI-compatible chat-completions provider for prototype cloud usage
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx

from infostream.core.logging import get_logger, sanitize_for_log
from infostream.digest.schema import DIGEST_JSON_SCHEMA
from infostream.models.schemas import ItemRecord, LlmConfig


def default_prompt_template() -> str:
    """Return the default summarize prompt template with placeholder tokens."""
    return (
        "You are an assistant that outputs JSON only.\n"
        "Given canonical items and user interests, return a digest JSON object with keys:\n"
        "version, new_communications, important_dates_events, interests, links, entities, relations, extraneous.\n"
        "All citations must be item_ref values from the provided items.\n"
        "Only use source_refs from this allowlist: {valid_item_refs_json}\n"
        "For entities and relations: either return [] OR include non-empty source_refs arrays with allowlisted refs.\n"
        "If no items are provided, still return a fully valid object with empty collections.\n"
        "Interests: {interests_text}\n"
        "Items: {items_json}\n"
    )


def default_repair_prompt_template() -> str:
    """Return the default repair prompt template with placeholder tokens."""
    return (
        "{base_prompt}\n"
        "Your previous output failed validation.\n"
        "Validation errors: {errors}\n"
        "Previous output: {invalid_output}\n"
        "Return corrected JSON only.\n"
    )


class SummarizerProvider(ABC):
    """Abstract provider contract for summarize/repair operations."""

    @abstractmethod
    def summarize(
        self,
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        raise NotImplementedError

    def get_last_exchange(self) -> dict[str, Any] | None:
        """Return provider-specific request/response metadata for debugging."""
        return None


class StubSummarizerProvider(SummarizerProvider):
    """Deterministic provider used for v1 while real model integrations are out of scope."""

    def summarize(
        self,
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        logger = get_logger("digest.provider.stub")
        logger.debug(
            "stub_summarize_started",
            message="stub summarizer invoked",
            item_count=len(items),
            llm_unique_id=llm.unique_id,
        )
        _ = llm
        _ = advanced
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
        output = json.dumps(payload)
        logger.debug("stub_summarize_completed", message="stub summarizer produced output")
        return output

    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        _ = invalid_output
        _ = errors
        return self.summarize(items, interests_text, llm, advanced)

    def _parse_interests(self, interests_text: str) -> list[str]:
        tokens = [token.strip() for token in interests_text.replace("\n", ",").split(",")]
        clean = [token for token in tokens if token]
        if clean:
            return clean
        return ["general"]


class OpenAICompatibleProvider(SummarizerProvider):
    """OpenAI-compatible provider using the /chat/completions API shape."""

    def __init__(self) -> None:
        self._last_exchange: dict[str, Any] | None = None

    def get_last_exchange(self) -> dict[str, Any] | None:
        return self._last_exchange

    def summarize(
        self,
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        payload = self._call_chat_completions(
            llm=llm,
            prompt=self._build_prompt(items, interests_text, advanced),
            advanced=advanced,
        )
        return payload

    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        repair_prompt = self._build_repair_prompt(
            invalid_output, errors, items, interests_text, advanced
        )
        return self._call_chat_completions(llm=llm, prompt=repair_prompt, advanced=advanced)

    def _call_chat_completions(self, llm: LlmConfig, prompt: str, advanced: dict[str, Any]) -> str:
        """Submit chat completion request and return assistant text content."""
        logger = get_logger("digest.provider.openai")
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
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return JSON only. Do not include markdown, prose, or code fences. "
                        "All keys must exist with the expected value types."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        use_json_schema = bool(advanced.get("use_json_schema_response", True))
        if use_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "infostream_digest",
                    "schema": DIGEST_JSON_SCHEMA,
                    "strict": True,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
        logger.info(
            "openai_request_started",
            message="calling openai-compatible chat completions",
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            has_organization=isinstance(organization, str) and bool(organization),
            use_json_schema=use_json_schema,
        )

        with httpx.Client(timeout=timeout_seconds) as client:
            response = self._post_chat_request(client, base_url, headers, body, use_json_schema)
            data = response.json()
        logger.info(
            "openai_request_completed",
            message="openai-compatible request completed",
            status_code=response.status_code,
            request_id=response.headers.get("x-request-id"),
            response_keys=sanitize_for_log(list(data.keys())),
        )
        self._last_exchange = {
            "provider": "openai_compatible",
            "request": {
                "url": f"{base_url}/chat/completions",
                "model": model,
                "timeout_seconds": timeout_seconds,
                "use_json_schema_response": use_json_schema,
            },
            "response": {
                "status_code": response.status_code,
                "request_id": response.headers.get("x-request-id"),
                "processing_ms": response.headers.get("openai-processing-ms"),
                "body": sanitize_for_log(data),
            },
        }

        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if isinstance(content, list):
            # Some OpenAI-compatible gateways return structured content arrays.
            text_parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            content = "".join(text_parts)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenAI-compatible provider returned empty content")
        return content

    def _post_chat_request(
        self,
        client: httpx.Client,
        base_url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        use_json_schema: bool,
    ) -> httpx.Response:
        """Send one chat request and fallback to json_object when needed."""
        logger = get_logger("digest.provider.openai")
        response = client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        try:
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError:
            if not use_json_schema:
                raise
            if response.status_code not in {400, 404, 422}:
                raise
            logger.warning(
                "openai_request_retry_json_object",
                message="retrying openai request with json_object response format",
                status_code=response.status_code,
            )
            retry_body = dict(body)
            retry_body["response_format"] = {"type": "json_object"}
            retry_response = client.post(
                f"{base_url}/chat/completions",
                json=retry_body,
                headers=headers,
            )
            retry_response.raise_for_status()
            return retry_response

    def _build_prompt(
        self,
        items: list[ItemRecord],
        interests_text: str,
        advanced: dict[str, Any],
    ) -> str:
        """Build summarize prompt using canonical item data and user interests."""
        max_items = int(advanced.get("max_items_in_prompt", 50))
        serializable_items = [item.model_dump() for item in items[:max_items]]
        payload = json.dumps(serializable_items, ensure_ascii=True)
        valid_item_refs_json = json.dumps([item.item_ref for item in items[:max_items]], ensure_ascii=True)

        template = str(advanced.get("prompt_template", "")).strip() or default_prompt_template()
        return self._render_template(
            template,
            {
                "{interests_text}": interests_text,
                "{items_json}": payload,
                "{valid_item_refs_json}": valid_item_refs_json,
            },
        )

    def _build_repair_prompt(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        advanced: dict[str, Any],
    ) -> str:
        """Build repair prompt from validation errors and previous invalid output."""
        error_text = "; ".join(errors)
        template = (
            str(advanced.get("repair_prompt_template", "")).strip() or default_repair_prompt_template()
        )
        return self._render_template(
            template,
            {
                "{base_prompt}": self._build_prompt(items, interests_text, advanced),
                "{invalid_output}": invalid_output,
                "{errors}": error_text,
            },
        )

    def _render_template(self, template: str, replacements: dict[str, str]) -> str:
        """Replace known placeholders without invoking unsafe format interpolation."""
        rendered = template
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)
        return rendered


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
        self._last_provider: SummarizerProvider | None = None

    def summarize(
        self,
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        provider = self._select_provider(llm)
        self._last_provider = provider
        return provider.summarize(items, interests_text, llm, advanced)

    def repair(
        self,
        invalid_output: str,
        errors: list[str],
        items: list[ItemRecord],
        interests_text: str,
        llm: LlmConfig,
        advanced: dict[str, Any],
    ) -> str:
        provider = self._select_provider(llm)
        self._last_provider = provider
        return provider.repair(invalid_output, errors, items, interests_text, llm, advanced)

    def get_last_exchange(self) -> dict[str, Any] | None:
        if self._last_provider is None:
            return None
        return self._last_provider.get_last_exchange()

    def _select_provider(self, llm: LlmConfig) -> SummarizerProvider:
        """Select provider in a way that can be expanded without API changes."""
        logger = get_logger("digest.provider.router")
        if not llm.unique_id:
            logger.warning(
                "provider_select_failed", message="provider selection failed: llm not configured"
            )
            raise RuntimeError("LLM is not configured")

        if llm.unique_id in {"stub-local", "stub-cloud", "stub"} or llm.name.startswith("stub"):
            logger.debug(
                "provider_selected",
                message="provider selected stub",
                provider="stub",
                llm_unique_id=llm.unique_id,
            )
            return self._stub_provider
        logger.debug(
            "provider_selected",
            message="provider selected openai-compatible",
            provider="openai_compatible",
            llm_unique_id=llm.unique_id,
        )
        return self._openai_provider
