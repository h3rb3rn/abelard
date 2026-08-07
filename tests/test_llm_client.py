"""Tests for the LLM client layer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.llm_client import LLMClient, LLMResponse, Message, UsageInfo


class TestUsageInfo:
    def test_default_values(self) -> None:
        usage = UsageInfo()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.cost_usd == 0.0

    def test_cost_estimation_gpt4omini(self) -> None:
        # Manually set values and check _estimate_cost logic inline
        from services.llm_client import _estimate_cost
        usage = UsageInfo(prompt_tokens=1_000_000, completion_tokens=500_000)
        _estimate_cost("openai", "gpt-4o-mini", usage)
        # 0.15 * 1 + 0.60 * 0.5 = 0.15 + 0.30 = 0.45
        assert usage.cost_usd == pytest.approx(0.45, rel=1e-3)


class TestLLMClientInit:
    def test_openai_defaults(self) -> None:
        client = LLMClient(provider="openai", model="gpt-4o-mini")
        assert client.provider == "openai"
        assert client.model == "gpt-4o-mini"
        assert "api.openai.com" in client.base_url

    def test_ollama_defaults(self) -> None:
        client = LLMClient(provider="ollama", model="mistral:latest")
        assert client.provider == "ollama"
        assert client.model == "mistral:latest"
        assert client.temperature == 0.7


class TestFromConfig:
    def test_creates_client(self) -> None:
        cfg = {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
            "temperature": 0.3,
        }
        client = LLMClient.from_config(
            provider="openai",
            config=cfg,
        )
        assert client.model == "gpt-4o"
        assert client.temperature == 0.3


class TestLLMResponse:
    def test_creation(self) -> None:
        resp = LLMResponse(text="hello")
        assert resp.text == "hello"
        assert isinstance(resp.usage, UsageInfo)


class TestExtractText:
    """Reasoning-Modelle (qwen3.6, deepseek-r1) fuellen 'content' erst nach der Gedankenkette."""

    def test_plain_content(self) -> None:
        from services.llm_client import _extract_text

        assert _extract_text({"message": {"content": "Hallo Welt"}}) == "Hallo Welt"

    def test_strips_inline_think_block(self) -> None:
        from services.llm_client import _extract_text

        choice = {"message": {"content": "<think>Ueberlegung…</think>\nDie Antwort lautet 42."}}
        assert _extract_text(choice) == "Die Antwort lautet 42."

    def test_falls_back_to_reasoning_when_content_empty(self) -> None:
        from services.llm_client import _extract_text

        choice = {"message": {"content": "", "reasoning": "Gedankenkette ohne Abschluss"}}
        assert _extract_text(choice) == "Gedankenkette ohne Abschluss"

    def test_falls_back_to_reasoning_content_field(self) -> None:
        from services.llm_client import _extract_text

        choice = {"message": {"content": None, "reasoning_content": "Alternative Feldbezeichnung"}}
        assert _extract_text(choice) == "Alternative Feldbezeichnung"

    def test_content_wins_over_reasoning(self) -> None:
        from services.llm_client import _extract_text

        choice = {"message": {"content": "Finale Antwort", "reasoning": "interne Gedanken"}}
        assert _extract_text(choice) == "Finale Antwort"

    def test_empty_message_returns_empty_string(self) -> None:
        from services.llm_client import _extract_text

        assert _extract_text({"message": {}}) == ""
        assert _extract_text({}) == ""


def test_message_dump() -> None:
    """Message (dataclass) should produce valid chat message dicts."""
    from dataclasses import asdict

    msg = Message(role="system", content="test")
    assert asdict(msg) == {"role": "system", "content": "test"}
