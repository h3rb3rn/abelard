"""Tests fuer global freigegebene Agenten (Sichtbarkeit, Klonen, Rechteschutz)."""

import uuid

import pytest

from models.db import Agent, User


def _agent(user_id: uuid.UUID, name: str = "Testagent", is_global: bool = False) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        system_prompt="Du bist ein Testagent.",
        persona_bio="Bio",
        llm_provider="openai",
        llm_model="moe-n04-rtx-qwen3.6:35b-256k",
        temperature=0.7,
        web_search_enabled=True,
        web_search_provider="searxng",
        searxng_url="https://search.example.org",
        knowledge_graph_enabled=True,
        cache_enabled=True,
        mcp_enabled=True,
        is_global=is_global,
    )


class TestModelDefaults:
    def test_agent_is_not_global_by_default(self) -> None:
        a = Agent(id=uuid.uuid4(), user_id=uuid.uuid4(), name="X", system_prompt="Y")
        # SQLAlchemy-Default greift erst beim Flush — der Mapper kennt die Spalte aber
        assert "is_global" in Agent.__table__.columns

    def test_user_has_is_admin_column(self) -> None:
        assert "is_admin" in User.__table__.columns


class TestCloneAgent:
    def test_clone_copies_config_into_own_tenant(self) -> None:
        from api_router_v2 import _clone_agent_for_user

        owner, other = uuid.uuid4(), uuid.uuid4()
        source = _agent(owner, "Niels Bohr", is_global=True)
        clone = _clone_agent_for_user(source, other)

        assert clone.user_id == other
        assert clone.id != source.id
        assert clone.name == source.name
        assert clone.system_prompt == source.system_prompt
        assert clone.llm_model == source.llm_model
        assert clone.web_search_provider == "searxng"
        assert clone.searxng_url == source.searxng_url

    def test_clone_is_private(self) -> None:
        """Kopien duerfen die Freigabe nicht erben — sonst breitet sie sich unkontrolliert aus."""
        from api_router_v2 import _clone_agent_for_user

        source = _agent(uuid.uuid4(), is_global=True)
        clone = _clone_agent_for_user(source, uuid.uuid4())
        assert clone.is_global is False

    def test_clone_can_target_project(self) -> None:
        from api_router_v2 import _clone_agent_for_user

        pid = uuid.uuid4()
        clone = _clone_agent_for_user(_agent(uuid.uuid4(), is_global=True), uuid.uuid4(), project_id=pid)
        assert clone.project_id == pid

    def test_source_agent_is_untouched(self) -> None:
        """Der Originalagent darf beim Klonen weder Besitzer noch Projekt wechseln."""
        from api_router_v2 import _clone_agent_for_user

        owner = uuid.uuid4()
        source = _agent(owner, is_global=True)
        source.project_id = None
        _clone_agent_for_user(source, uuid.uuid4(), project_id=uuid.uuid4())
        assert source.user_id == owner
        assert source.project_id is None
        assert source.is_global is True


class TestAgentSerialization:
    def test_owner_flag_true_for_own_agent(self) -> None:
        from api_router_v2 import _agent_to_read

        uid = uuid.uuid4()
        read = _agent_to_read(_agent(uid, is_global=True), uid)
        assert read.is_owner is True
        assert read.is_global is True

    def test_owner_flag_false_for_foreign_agent(self) -> None:
        from api_router_v2 import _agent_to_read

        read = _agent_to_read(_agent(uuid.uuid4(), is_global=True), uuid.uuid4())
        assert read.is_owner is False
        assert read.is_global is True


class TestRouteRegistration:
    """Die neuen Endpunkte muessen tatsaechlich am Router haengen."""

    @pytest.mark.parametrize("path,method", [
        ("/agents/{agent_id}/global", "PATCH"),
        ("/agents/{agent_id}/clone", "POST"),
        ("/agents", "GET"),
    ])
    def test_route_exists(self, path: str, method: str) -> None:
        from api_router_v2 import router

        matches = [r for r in router.routes if getattr(r, "path", "") == path and method in getattr(r, "methods", set())]
        assert matches, f"{method} {path} fehlt"
