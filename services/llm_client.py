"""Unified async LLM client supporting Ollama and OpenAI backends."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)


@dataclass
class UsageInfo:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


# Token-price constants (USD per 1M tokens)
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-sonnet": (3.00, 15.00),
}

# Ollama pricing is effectively zero (local hardware)
_OLLAMA_PRICE: tuple[float, float] = (0.0, 0.0)


def _estimate_cost(provider: str, model: str, usage: UsageInfo) -> None:
    if provider == "ollama":
        usage.cost_usd = 0.0
        return
    prices = _PRICING.get(model, (0.50, 2.50))
    usage.cost_usd = (usage.prompt_tokens / 1_000_000) * prices[0] + \
                     (usage.completion_tokens / 1_000_000) * prices[1]


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _extract_text(choice: dict[str, Any]) -> str:
    """Extrahiere die Antwort aus einer Chat-Completion-Choice.

    Reasoning-Modelle (z.B. qwen3.6, deepseek-r1) legen ihre Gedankenkette in ein
    separates Feld ``reasoning``/``reasoning_content`` und lassen ``content`` leer,
    wenn das Token-Budget vor der eigentlichen Antwort aufgebraucht ist. In dem Fall
    ist die Gedankenkette besser als gar nichts — sonst entstuenden leere Redebeitraege.
    Inline-``<think>``-Bloecke werden immer entfernt.
    """
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    if content:
        return _THINK_BLOCK.sub("", content).strip() or content

    for key in ("reasoning", "reasoning_content"):
        fallback = (message.get(key) or "").strip()
        if fallback:
            logger.warning(
                "LLM lieferte leeres 'content' — nutze '%s' als Fallback (Token-Budget zu klein "
                "fuer ein Reasoning-Modell?)", key,
            )
            return fallback
    return ""


@dataclass
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    text: str
    usage: UsageInfo = field(default_factory=UsageInfo)


class LLMClient:
    """Provider-agnostic async wrapper over OpenAI-compatible and Ollama APIs."""

    def __init__(
        self,
        provider: Literal["openai", "ollama"],
        model: str,
        base_url: str = "",
        api_key: str = "",
        temperature: float = 0.7,
        presence_penalty: float = 0.6,
        frequency_penalty: float = 0.6,
        keep_alive: str | int = -1,
    ) -> None:
        if provider == "openai":
            self.provider = "openai"
            self.model = model
            self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
            self.api_key = api_key
        else:
            self.provider = "ollama"
            self.model = model
            self.base_url = (base_url or "http://ollama:11430/v1").rstrip("/")
            self.api_key = api_key  # unused for Ollama
        self.temperature = temperature
        self.presence_penalty = presence_penalty
        self.frequency_penalty = frequency_penalty
        self.keep_alive = keep_alive

    async def chat(
        self,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
    ) -> LLMResponse:
        """Execute a chat completion and return structured response."""
        p_pen = presence_penalty if presence_penalty is not None else self.presence_penalty
        f_pen = frequency_penalty if frequency_penalty is not None else self.frequency_penalty
        if self.provider == "openai":
            return await self._chat_openai(messages, max_tokens=max_tokens, presence_penalty=p_pen, frequency_penalty=f_pen)
        return await self._chat_ollama(messages, max_tokens=max_tokens, presence_penalty=p_pen, frequency_penalty=f_pen)

    async def _chat_openai(
        self,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        presence_penalty: float = 0.6,
        frequency_penalty: float = 0.6,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "presence_penalty": presence_penalty,
            "frequency_penalty": frequency_penalty,
            "keep_alive": self.keep_alive,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key and self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"

        max_attempts = 5
        delay = 30.0
        last_exc = None

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                choice = data.get("choices")
                if not choice:
                    raise RuntimeError(f"LLM returned no choices: {data}")
                text = _extract_text(choice[0])
                usage_data = data.get("usage") or {}
                usage = UsageInfo(
                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                    completion_tokens=usage_data.get("completion_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                )
                _estimate_cost("openai", self.model, usage)
                logger.debug("OpenAI token usage: %s total — cost $%.6f", usage.total_tokens, usage.cost_usd)
                return LLMResponse(text=text, usage=usage)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "🔄 LLM HTTP Call [%s/%s] failed (Attempt %d/%d): %s — Retrying in %.1fs...",
                    self.provider, self.model, attempt, max_attempts, exc, delay
                )
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                    delay *= 2

        raise RuntimeError(f"LLM API connection exhausted after {max_attempts} attempts: {last_exc}") from last_exc

    async def _chat_ollama(
        self,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        presence_penalty: float = 0.6,
        frequency_penalty: float = 0.6,
    ) -> LLMResponse:
        # Ollama /v1/chat uses same payload as OpenAI
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "presence_penalty": presence_penalty,
            "frequency_penalty": frequency_penalty,
            "keep_alive": self.keep_alive,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        max_attempts = 5
        delay = 30.0
        last_exc = None

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                choice = data.get("choices")
                if not choice:
                    raise RuntimeError(f"LLM returned no choices: {data}")
                text = _extract_text(choice[0])
                usage_data = data.get("usage") or {}
                usage = UsageInfo(
                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                    completion_tokens=usage_data.get("completion_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                )
                _estimate_cost("ollama", self.model, usage)
                return LLMResponse(text=text, usage=usage)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "🔄 Ollama HTTP Call [%s/%s] failed (Attempt %d/%d): %s — Retrying in %.1fs...",
                    self.provider, self.model, attempt, max_attempts, exc, delay
                )
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                    delay *= 2

        raise RuntimeError(f"Ollama API connection exhausted after {max_attempts} attempts: {last_exc}") from last_exc

    @staticmethod
    def from_config(
        provider: Literal["openai", "ollama"],
        config: dict[str, Any],
    ) -> LLMClient:
        return LLMClient(
            provider=provider,
            model=config["model"],
            base_url=config.get("base_url", ""),
            api_key=config.get("api_key", ""),
            temperature=config.get("temperature", 0.7),
            keep_alive=config.get("keep_alive", -1),
        )
