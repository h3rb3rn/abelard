"""Tests for the debate orchestrator's core logic."""

import pytest

from config import settings
from engine.orchestrator import (
    MODERATOR_NAME,
    DebateOrchestrator,
    DefaultAgentRole,
    ROLE_PROMPTS,
)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Chroma/Upload-Verzeichnisse in tmp umleiten — kein Zugriff auf /chroma-data."""
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path / "chroma"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.chdir(tmp_path)


def _make_orb(session_id: str = "test-123", motion: str = "Test motion") -> DebateOrchestrator:
    return DebateOrchestrator(session_id=session_id, motion=motion)


class TestAgentRoles:
    def test_all_roles_defined(self) -> None:
        roles = [r.value for r in DefaultAgentRole]
        expected = ["thesis", "anti_thesis", "synthesizer", "facilitator", "researcher"]
        assert sorted(roles) == sorted(expected)

    def test_role_prompt_exists(self) -> None:
        for role in DefaultAgentRole:
            assert role in ROLE_PROMPTS
            assert len(ROLE_PROMPTS[role]) > 50


class TestOrchestratorInit:
    def test_orchestrator_creation(self) -> None:
        orb = _make_orb()
        assert orb.session_id == "test-123"
        assert orb.motion == "Test motion"

    def test_default_agents_empty(self) -> None:
        orb = _make_orb()
        assert orb.state.agents == []

    def test_session_scoped_state_keys(self) -> None:
        orb = _make_orb(session_id="abc")
        assert "abc" in orb.state_mgr.KEY_COST_CENTRAL
        assert "abc" in orb.state_mgr.KEY_TURN_COUNTER


class TestContextBuilding:
    def test_empty_context(self) -> None:
        orb = _make_orb()
        ctx = orb._build_context()
        assert "keine Vorredner" in ctx

    def test_recent_turns_full_text(self) -> None:
        orb = _make_orb()
        orb.state.turns = [
            ("thesis", "Argument 1 zur Motion."),
            ("anti_thesis", "Gegenargument 1 zur Motion."),
        ]
        ctx = orb._build_context()
        assert "Argument 1 zur Motion." in ctx
        assert "Gegenargument 1 zur Motion." in ctx

    def test_older_turns_appear_as_history(self) -> None:
        """Kein Amnesie-Fenster mehr: alte Turns bleiben als Kernpunkte sichtbar."""
        orb = _make_orb()
        orb.state.turns = [(f"agent{i}", f"Einzigartige Kernthese Nummer {i} über Freiheit.") for i in range(10)]
        ctx = orb._build_context()
        assert "KERNPUNKT-HISTORIE" in ctx
        # Der allererste Turn ist trotz 10 Folge-Turns noch referenziert
        assert "Nummer 0" in ctx

    def test_moderator_correction_lands_in_context(self) -> None:
        orb = _make_orb()
        orb.state.turns = [
            ("thesis", "These."),
            (MODERATOR_NAME, "KORREKTUR AN ALLE: zurück zur Motion."),
        ]
        ctx = orb._build_context()
        assert "KORREKTUR AN ALLE" in ctx


class TestRepetitionDetection:
    def test_no_turns_no_repetition(self) -> None:
        orb = _make_orb()
        assert orb._detect_repetition("Irgendein neuer Text mit vielen Worten hier drin") is False

    def test_detects_verbatim_repetition(self) -> None:
        orb = _make_orb()
        text = "Die Würde des Menschen ist unantastbar und bildet das Fundament aller Ethik in der Moderne."
        orb.state.turns = [("thesis", text)]
        assert orb._detect_repetition(text) is True

    def test_ignores_old_turns_beyond_lookback(self) -> None:
        """Kumulative Historie zählt nicht mehr — nur die jüngsten Turns."""
        orb = _make_orb()
        repeated = "Die Würde des Menschen ist unantastbar und bildet das Fundament aller Ethik in der Moderne."
        filler = [
            (f"agent{i}", f"Völlig anderer eigenständiger Beitrag Nummer {i} mit individuellen Formulierungen und Beispielen aus Bereich {i}.")
            for i in range(10)
        ]
        orb.state.turns = [("thesis", repeated)] + filler
        assert orb._detect_repetition(repeated) is False

    def test_markdown_headings_not_counted(self) -> None:
        orb = _make_orb()
        orb.state.turns = [("thesis", "## Mein Argument\nInhalt A über Freiheit und Rechte im Staat heute.")]
        new = "## Mein Argument\nVöllig anderer Inhalt über Wirtschaft und Klimapolitik in Europa morgen."
        assert orb._detect_repetition(new) is False

    def test_moderator_turns_excluded(self) -> None:
        orb = _make_orb()
        note = "KORREKTUR AN ALLE: bitte zurück zum Kern der Motion und ihre Implikationen diskutieren."
        orb.state.turns = [(MODERATOR_NAME, note)]
        assert orb._detect_repetition(note) is False


class TestModeratorParsing:
    def test_valid_json(self) -> None:
        json_str = '{"status": "CONSENSUS", "reason": "all agreed"}'
        result = DebateOrchestrator._parse_moderator_json(json_str)
        assert result["status"] == "CONSENSUS"

    def test_markdown_fenced_json(self) -> None:
        json_str = '```json\n{"status": "CONTINUE", "reason": "keep going"}\n```'
        result = DebateOrchestrator._parse_moderator_json(json_str)
        assert result["status"] == "CONTINUE"

    def test_invalid_json_returns_default(self) -> None:
        result = DebateOrchestrator._parse_moderator_json("not json at all")
        assert result["status"] == "CONTINUE"

    def test_message_maps_to_correction(self) -> None:
        json_str = '{"status": "CORRECTION", "message": "Zurück zum Thema", "direction": "Ethik"}'
        result = DebateOrchestrator._parse_moderator_json(json_str)
        assert result["correction"] == "Zurück zum Thema"
        assert result["direction"] == "Ethik"


class TestModeratorContext:
    def test_moderator_sees_whole_debate(self) -> None:
        orb = _make_orb()
        orb.state.turns = [(f"agent{i}", f"Beitrag {i} über Aspekt {i}.") for i in range(8)]
        ctx = orb._build_moderator_context()
        assert "Beitrag 0" in ctx
        assert "Beitrag 7" in ctx


class TestSessionStatus:
    def test_initial_state_fields(self) -> None:
        orb = _make_orb(session_id="s1", motion="test motion")
        assert orb.state.status == "ACTIVE"
        assert orb.state.session_id == "s1"
        assert orb.state.turns == []
