"""Tests fuer Export und Import von Agenten."""

import json
import pathlib
import uuid

import pytest

from models.db import Agent
from services.agent_transfer_service import (
    DEPLOYMENT_FIELDS,
    MAX_IMPORT_AGENTS,
    SCHEMA_VERSION,
    ImportValidationError,
    agent_to_dict,
    build_bundle,
    contains_private_address,
    parse_bundle,
)


def _agent(name: str = "Testagent") -> Agent:
    return Agent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name=name,
        system_prompt="Du bist ein Testagent.",
        persona_bio="Kurzbiografie",
        llm_provider="openai",
        llm_base_url="https://api.intern.example/v1",
        llm_model="modell-xy",
        temperature=0.4,
        web_search_enabled=True,
        web_search_provider="searxng",
        searxng_url="https://search.intern.example",
        knowledge_graph_enabled=True,
        cache_enabled=True,
        mcp_enabled=False,
    )


class TestExport:
    def test_portable_omits_deployment_fields(self) -> None:
        """Portable Exporte duerfen keine installationsspezifischen Werte tragen."""
        data = agent_to_dict(_agent(), portable=True)
        for field in DEPLOYMENT_FIELDS:
            assert field not in data

    def test_portable_keeps_persona(self) -> None:
        data = agent_to_dict(_agent("Ada Lovelace"), portable=True)
        assert data["name"] == "Ada Lovelace"
        assert data["system_prompt"] == "Du bist ein Testagent."
        assert data["persona_bio"] == "Kurzbiografie"
        assert data["temperature"] == pytest.approx(0.4)
        assert data["web_search_enabled"] is True
        assert data["web_search_provider"] == "searxng"

    def test_full_export_includes_deployment_fields(self) -> None:
        data = agent_to_dict(_agent(), portable=False)
        assert data["llm_model"] == "modell-xy"
        assert data["llm_base_url"] == "https://api.intern.example/v1"

    def test_no_api_key_field_anywhere(self) -> None:
        """Schluessel haengen am Endpoint, nie am Agenten — auch nicht im Export."""
        for portable in (True, False):
            blob = json.dumps(agent_to_dict(_agent(), portable=portable)).lower()
            assert "api_key" not in blob
            assert "secret" not in blob

    def test_bundle_metadata(self) -> None:
        b = build_bundle([_agent("A"), _agent("B")], portable=True, source="test")
        assert b["schema_version"] == SCHEMA_VERSION
        assert b["count"] == 2
        assert b["portable"] is True
        assert b["source"] == "test"
        assert "exported_at" in b


class TestPrivateAddressDetection:
    def test_detects_rfc1918(self) -> None:
        a = _agent()
        a.llm_base_url = "http://192.168.155.224:11435/v1"
        assert contains_private_address(build_bundle([a], portable=False))

    def test_detects_localhost(self) -> None:
        a = _agent()
        a.searxng_url = "http://localhost:8080"
        assert contains_private_address(build_bundle([a], portable=False))

    def test_public_addresses_are_fine(self) -> None:
        a = _agent()
        a.llm_base_url = "https://api.openai.com/v1"
        a.searxng_url = "https://search.example.org"
        assert contains_private_address(build_bundle([a], portable=False)) == []

    def test_portable_bundle_cannot_leak(self) -> None:
        """Auch bei internen URLs am Agenten bleibt der portable Export sauber."""
        a = _agent()
        a.llm_base_url = "http://10.0.0.5:8000"
        a.searxng_url = "http://192.168.1.1"
        assert contains_private_address(build_bundle([a], portable=True)) == []


class TestParseBundle:
    def test_accepts_full_bundle(self) -> None:
        b = build_bundle([_agent("X")], portable=True)
        assert [e["name"] for e in parse_bundle(b)] == ["X"]

    def test_accepts_bare_list(self) -> None:
        entries = parse_bundle([{"name": "Y", "system_prompt": "Prompt"}])
        assert entries[0]["name"] == "Y"

    def test_rejects_unknown_schema_version(self) -> None:
        with pytest.raises(ImportValidationError, match="schema_version"):
            parse_bundle({"schema_version": 99, "agents": [{"name": "X"}]})

    def test_rejects_empty(self) -> None:
        with pytest.raises(ImportValidationError, match="keine Agenten"):
            parse_bundle({"agents": []})

    def test_rejects_missing_name(self) -> None:
        with pytest.raises(ImportValidationError, match="keinen Namen"):
            parse_bundle({"agents": [{"system_prompt": "nur Prompt"}]})

    def test_rejects_wrong_type(self) -> None:
        with pytest.raises(ImportValidationError):
            parse_bundle("Zeichenkette")

    def test_rejects_oversized_bundle(self) -> None:
        many = [{"name": f"A{i}"} for i in range(MAX_IMPORT_AGENTS + 1)]
        with pytest.raises(ImportValidationError, match="Zu viele"):
            parse_bundle({"agents": many})

    def test_unknown_fields_are_dropped(self) -> None:
        """Fremde Felder duerfen nicht ungeprueft ins Datenmodell wandern."""
        entries = parse_bundle({"agents": [{"name": "X", "is_global": True, "user_id": "fremd", "id": "x"}]})
        assert "is_global" not in entries[0]
        assert "user_id" not in entries[0]
        assert "id" not in entries[0]

    def test_temperature_is_clamped(self) -> None:
        assert parse_bundle({"agents": [{"name": "X", "temperature": 99}]})[0]["temperature"] == 2.0
        assert parse_bundle({"agents": [{"name": "X", "temperature": -5}]})[0]["temperature"] == 0.0
        assert parse_bundle({"agents": [{"name": "X", "temperature": "unsinn"}]})[0]["temperature"] == 0.7

    def test_invalid_search_provider_falls_back(self) -> None:
        e = parse_bundle({"agents": [{"name": "X", "web_search_provider": "google"}]})[0]
        assert e["web_search_provider"] == "duckduckgo"

    def test_missing_prompt_gets_default(self) -> None:
        e = parse_bundle({"agents": [{"name": "Sokrates"}]})[0]
        assert "Sokrates" in e["system_prompt"]

    def test_long_values_are_truncated(self) -> None:
        e = parse_bundle({"agents": [{"name": "N" * 500, "system_prompt": "P" * 50000}]})[0]
        assert len(e["name"]) <= 128
        assert len(e["system_prompt"]) <= 20000


class TestSeedFile:
    SEED = pathlib.Path(__file__).resolve().parents[1] / "seeds" / "agents.json"

    def test_seed_file_exists(self) -> None:
        assert self.SEED.exists(), "seeds/agents.json fehlt"

    def test_seed_parses(self) -> None:
        entries = parse_bundle(json.loads(self.SEED.read_text(encoding="utf-8")))
        assert len(entries) >= 50

    def test_seed_is_portable_and_clean(self) -> None:
        """Die ausgelieferte Seed-Datei darf keine internen Adressen enthalten."""
        bundle = json.loads(self.SEED.read_text(encoding="utf-8"))
        assert bundle["portable"] is True
        assert contains_private_address(bundle) == []
        for agent in bundle["agents"]:
            for field in DEPLOYMENT_FIELDS:
                assert field not in agent, f"{agent['name']} traegt {field}"

    def test_seed_has_no_duplicate_names(self) -> None:
        names = [a["name"] for a in json.loads(self.SEED.read_text(encoding="utf-8"))["agents"]]
        assert len(names) == len(set(names))

    def test_seed_is_deterministic(self) -> None:
        """Ohne Zeitstempel und alphabetisch sortiert — sonst rauscht jeder Diff."""
        bundle = json.loads(self.SEED.read_text(encoding="utf-8"))
        assert "exported_at" not in bundle
        names = [a["name"] for a in bundle["agents"]]
        assert names == sorted(names)


class TestRouteRegistration:
    @pytest.mark.parametrize("path,method", [
        ("/agents/export", "GET"),
        ("/agents/import", "POST"),
        ("/agents/import/seed", "POST"),
    ])
    def test_route_exists(self, path: str, method: str) -> None:
        from api_router_v2 import router

        assert [r for r in router.routes
                if getattr(r, "path", "") == path and method in getattr(r, "methods", set())], \
            f"{method} {path} fehlt"

    def test_literal_routes_precede_parameterized(self) -> None:
        """/agents/export darf nicht von /agents/{agent_id} geschluckt werden."""
        from api_router_v2 import router

        paths = [getattr(r, "path", "") for r in router.routes]
        assert paths.index("/agents/export") < paths.index("/agents/{agent_id}")
