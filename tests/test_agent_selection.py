"""Tests fuer die KI-gestuetzte Agentenauswahl zum Debattenthema."""

from unittest.mock import AsyncMock

import pytest

from services.agent_selection_service import (
    AgentCandidate,
    build_catalog,
    heuristic_selection,
    parse_selection,
    select_agents_for_motion,
)
from services.llm_client import LLMResponse


def _candidates() -> list[AgentCandidate]:
    return [
        AgentCandidate(id="1", name="Charles Bennett", field="Quantencomputing",
                       bio="Quantenkryptographie, Zufallszahlen, Thermodynamik der Berechnung"),
        AgentCandidate(id="2", name="Geoffrey Hinton", field="Künstliche Intelligenz",
                       bio="Neuronale Netze, Backpropagation, Deep Learning"),
        AgentCandidate(id="3", name="Johann Wolfgang von Goethe", field="Dichtung",
                       bio="Farbenlehre, Faust, Weimarer Klassik"),
        AgentCandidate(id="4", name="Donald Knuth", field="Informatik",
                       bio="Algorithmenanalyse, Zufallszahlen testen, TAOCP"),
    ]


class TestCatalog:
    def test_numbered_and_compact(self) -> None:
        cat = build_catalog(_candidates())
        assert cat.startswith("1. Charles Bennett [Quantencomputing]")
        assert "4. Donald Knuth" in cat

    def test_bio_is_truncated(self) -> None:
        long = [AgentCandidate(id="x", name="X", bio="wort " * 500)]
        line = build_catalog(long)
        assert len(line) < 400


class TestParseSelection:
    def test_parses_valid_json(self) -> None:
        cands = _candidates()
        txt = '{"selection":[{"number":1,"reason":"Quantenzufall"},{"number":4,"reason":"RNG-Tests"}],"rationale":"gute Mischung"}'
        picks, rationale = parse_selection(txt, cands, 2)
        assert [p.candidate.name for p in picks] == ["Charles Bennett", "Donald Knuth"]
        assert picks[0].reason == "Quantenzufall"
        assert rationale == "gute Mischung"

    def test_handles_markdown_fences_and_prose(self) -> None:
        cands = _candidates()
        txt = 'Gerne!\n```json\n{"selection":[{"number":2,"reason":"KI"}]}\n```\nViel Erfolg.'
        picks, _ = parse_selection(txt, cands, 1)
        assert [p.candidate.name for p in picks] == ["Geoffrey Hinton"]

    def test_ignores_out_of_range_and_duplicates(self) -> None:
        cands = _candidates()
        txt = '{"selection":[{"number":99},{"number":0},{"number":1},{"number":1},{"number":2}]}'
        picks, _ = parse_selection(txt, cands, 4)
        assert [p.candidate.name for p in picks] == ["Charles Bennett", "Geoffrey Hinton"]

    def test_respects_count_limit(self) -> None:
        cands = _candidates()
        txt = '{"selection":[{"number":1},{"number":2},{"number":3},{"number":4}]}'
        picks, _ = parse_selection(txt, cands, 2)
        assert len(picks) == 2

    def test_garbage_returns_empty(self) -> None:
        assert parse_selection("kein json hier", _candidates(), 3) == ([], "")


class TestHeuristic:
    def test_prefers_topical_overlap(self) -> None:
        motion = "Wie testet man Zufallszahlen aus Quantencomputing mit neuronalen Netzen?"
        picks = heuristic_selection(motion, _candidates(), 2)
        names = [p.candidate.name for p in picks]
        assert "Johann Wolfgang von Goethe" not in names

    def test_returns_requested_count(self) -> None:
        assert len(heuristic_selection("Thema", _candidates(), 3)) == 3

    def test_deterministic(self) -> None:
        m = "Zufallszahlen und neuronale Netze"
        assert [p.candidate.name for p in heuristic_selection(m, _candidates(), 3)] == \
               [p.candidate.name for p in heuristic_selection(m, _candidates(), 3)]


class TestSelectAgentsForMotion:
    @pytest.mark.asyncio
    async def test_uses_llm_result(self) -> None:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=LLMResponse(
            text='{"selection":[{"number":1,"reason":"Quanten"},{"number":4,"reason":"Tests"}],"rationale":"passend"}'
        ))
        picks, rationale = await select_agents_for_motion("Quanten-Zufallszahlen", _candidates(), client, count=2)
        assert [p.candidate.name for p in picks] == ["Charles Bennett", "Donald Knuth"]
        assert rationale == "passend"

    @pytest.mark.asyncio
    async def test_falls_back_when_llm_raises(self) -> None:
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=RuntimeError("Gateway kaputt"))
        picks, rationale = await select_agents_for_motion("Zufallszahlen testen", _candidates(), client, count=2)
        assert len(picks) == 2
        assert "Heuristische Auswahl" in rationale

    @pytest.mark.asyncio
    async def test_falls_back_without_client(self) -> None:
        picks, rationale = await select_agents_for_motion("Thema", _candidates(), None, count=2)
        assert len(picks) == 2
        assert "kein LLM" in rationale

    @pytest.mark.asyncio
    async def test_tops_up_when_llm_returns_too_few(self) -> None:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=LLMResponse(text='{"selection":[{"number":1,"reason":"nur einer"}]}'))
        picks, _ = await select_agents_for_motion("Zufallszahlen", _candidates(), client, count=3)
        assert len(picks) == 3
        assert picks[0].candidate.name == "Charles Bennett"
        assert len({p.candidate.id for p in picks}) == 3  # keine Duplikate

    @pytest.mark.asyncio
    async def test_count_is_clamped_to_pool_size(self) -> None:
        picks, _ = await select_agents_for_motion("Thema", _candidates()[:2], None, count=10)
        assert len(picks) == 2

    @pytest.mark.asyncio
    async def test_empty_pool(self) -> None:
        picks, rationale = await select_agents_for_motion("Thema", [], None, count=3)
        assert picks == []
        assert "Keine Agenten" in rationale
