"""Tests for memory service components."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.memory_service import DebateTurn, TurnBuilder


class TestDebateTurn:
    def test_to_dict_includes_all_fields(self) -> None:
        turn = DebateTurn(
            turn_id="t-1",
            agent_name="test-agent",
            role="assistant",
            content="Hello world",
            round_num=1,
            timestamp="2026-07-08T00:00:00",
        )
        d = turn.to_dict()
        assert d["turn_id"] == "t-1"
        assert d["content"] == "Hello world"


class TestTurnBuilder:
    def test_build_creates_valid_turn(self) -> None:
        builder = TurnBuilder(agent_name="test", round_num=3)
        turn = builder.build("Test content")
        assert turn.agent_name == "test"
        assert turn.round_num == 3
        assert len(turn.turn_id) > 10  # UUID-ish

    def test_metadata_propagation(self) -> None:
        builder = TurnBuilder(agent_name="x", metadata_={"source": "manual"})
        turn = builder.build("y")
        assert turn.metadata_["source"] == "manual"


class TestMemoryServiceChromaInterface:
    @pytest.mark.asyncio
    async def test_semantic_search_scoped_to_session(self) -> None:
        """semantic_search filtert nach session_id und mappt Treffer korrekt."""
        from services.memory_service import MemoryService

        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["Hello world"]],
            "metadatas": [[{
                "turn_id": "t1",
                "agent_name": "x",
                "round_num": 1,
                "timestamp": "2026-07-08",
                "session_id": "s1",
            }]],
        }

        memory = MemoryService.__new__(MemoryService)
        memory.session_id = "s1"
        memory.collection = mock_collection

        results = await memory.semantic_search("Hello", top_k=1)
        assert len(results) == 1
        assert results[0]["document"] == "Hello world"
        assert results[0]["turn_id"] == "t1"
        # Query muss auf die Session gescoped sein
        _, kwargs = mock_collection.query.call_args
        assert kwargs["where"] == {"session_id": "s1"}


class TestConceptExtraction:
    def test_extracts_german_nouns(self) -> None:
        from services.memory_service import extract_concepts

        text = "Die Freiheit des Einzelnen endet an der Verantwortung gegenüber der Gesellschaft."
        concepts = extract_concepts(text)
        assert "Freiheit" in concepts
        assert "Gesellschaft" in concepts

    def test_filters_stopwords_and_short_words(self) -> None:
        from services.memory_service import extract_concepts

        concepts = extract_concepts("Aber Diese Dann Hier Und Oder")
        assert concepts == []

    def test_caps_concept_count(self) -> None:
        from services.memory_service import extract_concepts

        # 30 verschiedene kapitalisierte Kunstwoerter — Limit liegt bei 20
        long_text = " ".join("Konzept" + chr(97 + i % 26) * 5 for i in range(30))
        assert len(extract_concepts(long_text)) <= 20
