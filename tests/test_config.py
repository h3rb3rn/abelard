"""Tests for configuration loading."""

import os
from unittest.mock import patch

import pytest

from config import Settings


class TestSettings:
    def test_valkey_url_no_password(self) -> None:
        s = Settings(valkey_host="localhost", valkey_port=6379, valkey_password="")
        assert s.valkey_url == "redis://localhost:6379/0"

    def test_valkey_url_with_password(self) -> None:
        s = Settings(valkey_host="valkey", valkey_port=6379, valkey_password="secretpass")
        expected = "redis://:secretpass@valkey:6379/0"
        assert s.valkey_url == expected

    def test_searxng_search_url(self) -> None:
        s = Settings(searxng_base_url="http://searxng:8080")
        assert s.searxng_search_url == "http://searxng:8080/search?format=json"

    def test_defaults_are_valid(self) -> None:
        s = Settings()
        assert s.default_provider in ("openai", "ollama") or not s.openai_api_key
        assert 1 <= s.moderator_interval >= 1
        assert s.cost_threshold_usd > 0
        assert 0.0 <= s.default_temperature <= 2.0

    def test_llm_env_vars(self) -> None:
        """Verify config loads correctly from env-style keys."""
        with patch.dict(os.environ, {
            "DEFAULT_PROVIDER": "ollama",
            "OLLAMA_MODEL": "llama3:latest",
            "OPENAI_API_KEY": "",
        }):
            s = Settings(_env_file=None)
            assert s.default_provider == "ollama"
            assert s.ollama_model == "llama3:latest"
