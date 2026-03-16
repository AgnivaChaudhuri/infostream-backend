from __future__ import annotations

"""Unit tests for provider routing and default LLM configuration behavior."""

from pathlib import Path

import pytest

from infostream.core.config import BackendConfig
from infostream.core.files import ensure_system_layout, read_json
from infostream.core.paths import build_paths
from infostream.digest.provider import ProviderRouter, SummarizerProvider
from infostream.models.schemas import ItemRecord
from infostream.models.schemas import LlmConfig


class _FakeProvider(SummarizerProvider):
    """Simple fake provider used to assert routing behavior."""

    def __init__(self, text: str) -> None:
        self.text = text

    def summarize(self, items: list[ItemRecord], interests_text: str, llm: LlmConfig) -> str:
        _ = items
        _ = interests_text
        _ = llm
        return self.text

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
        _ = items
        _ = interests_text
        _ = llm
        return self.text


def test_provider_router_requires_configured_llm() -> None:
    router = ProviderRouter(stub_provider=_FakeProvider("stub"), openai_provider=_FakeProvider("openai"))
    with pytest.raises(RuntimeError):
        router.summarize([], "", LlmConfig())


def test_provider_router_uses_stub_provider_for_stub_ids() -> None:
    router = ProviderRouter(stub_provider=_FakeProvider("stub"), openai_provider=_FakeProvider("openai"))
    llm = LlmConfig(name="stub-local", mode="local", unique_id="stub-local", config={})
    assert router.summarize([], "", llm) == "stub"


def test_provider_router_uses_openai_provider_for_non_stub_ids() -> None:
    router = ProviderRouter(stub_provider=_FakeProvider("stub"), openai_provider=_FakeProvider("openai"))
    llm = LlmConfig(name="openai", mode="cloud", unique_id="gpt-4o-mini", config={})
    assert router.summarize([], "", llm) == "openai"


def test_system_layout_defaults_to_unconfigured_llm(tmp_path: Path) -> None:
    config = BackendConfig(home=tmp_path / "home")
    paths = build_paths(config)
    ensure_system_layout(paths, config)
    settings = read_json(paths.settings)
    assert settings["llm"]["unique_id"] == ""
