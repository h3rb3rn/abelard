"""Agent persistence layer scoped to User (tenant isolation)."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import Agent

logger = logging.getLogger(__name__)


class AgentService:
    """All queries against ``agents`` table scoped by user_id for tenant isolation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: uuid.UUID, limit: int = 50) -> list[Agent]:
        qs = (
            sa_select(Agent)
            .where(Agent.user_id == user_id)
            .order_by(Agent.name)
            .limit(max(1, min(limit, 200)))
        )
        res = await self._session.execute(qs)
        return list(res.scalars().all())

    async def find_one(self, id_: uuid.UUID) -> Agent | None:
        qs = sa_select(Agent).where(Agent.id == id_)
        res = await self._session.execute(qs)
        return res.scalar_one_or_none()

    async def find_one_by_user(self, user_id: uuid.UUID, name: str) -> Agent | None:
        qs = (
            sa_select(Agent)
            .where(Agent.user_id == user_id, Agent.name.ilike(name.strip()))
            .limit(1)
        )
        res = await self._session.execute(qs)
        return res.scalar_one_or_none()

    async def find_agents_by_ids(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Agent]:
        if not ids:
            return {}
        qs = sa_select(Agent).where(Agent.id.in_(ids))
        res = await self._session.execute(qs)
        return {a.id: a for a in res.scalars().all()}

    async def delete(self, id_: uuid.UUID) -> bool:
        agent = await self.find_one(id_)
        if agent is None:
            return False
        await self._session.delete(agent)
        return True

    async def create(
        self, *,
        user_id: uuid.UUID,
        name: str,
        system_prompt: str = "",
        llm_provider: str = "openai",
        llm_base_url: str = "",
        llm_model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        mcp_enabled: bool = False,
        mcp_server_config_json: dict | None = None,
        web_search_enabled: bool = False,
        search_provider: str = "searxng",
        knowledge_graph_enabled: bool = False,
        cache_enabled: bool = False,
        skills_json: dict | None = None,
    ) -> Agent:
        """Create an agent belonging to user_id with full V2 feature set."""
        if not name.strip():
            raise ValueError("agent.name must be non-blank")

        agent = Agent(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name.strip()[:64],
            system_prompt=(system_prompt or "").strip(),
            llm_provider=llm_provider or "openai",
            llm_base_url=(llm_base_url or "")[:512],
            llm_model=(llm_model or "gpt-4o-mini")[:128],
            temperature=max(0.0, min(2.0, temperature)),
            mcp_enabled=bool(mcp_enabled),
            mcp_server_config_json=mcp_server_config_json if mcp_server_config_json else None,
            web_search_enabled=bool(web_search_enabled),
            search_provider=search_provider or "searxng",
            knowledge_graph_enabled=bool(knowledge_graph_enabled),
            cache_enabled=bool(cache_enabled),
            skills_json=dict(skills_json) if skills_json else None,
        )
        self._session.add(agent)
        await self._session.flush()
        logger.info("Created agent '%s' (user=%s, kg=%s, search=%s)",
                     name, user_id, knowledge_graph_enabled, search_provider)
        return agent

    async def update(
        self, id_: uuid.UUID, **fields,
    ) -> Agent | None:
        agent = await self.find_one(id_)
        if agent is None:
            return None
        for key, val in fields.items():
            if hasattr(agent, key):
                setattr(agent, key, val)
        await self._session.flush()
        return agent