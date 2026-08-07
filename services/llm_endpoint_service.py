"""Service: User LLM-Endpoints verwalten pro Tenant."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Sequence, List
import httpx

from sqlalchemy import select as sa_select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from models.db import UserLLMEndpoint

logger = logging.getLogger(__name__)


async def test_endpoint_connection(provider: str, base_url: str = "", api_key: str = "") -> dict:
    """Tests if an LLM connection endpoint is online and reachable."""
    start_t = time.perf_counter()
    target_url = base_url.strip() if base_url else ("http://localhost:11434" if provider == "ollama" else "https://api.openai.com/v1")
    target_url = target_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if provider == "ollama":
                r = await client.get(f"{target_url}/api/tags")
                latency_ms = int((time.perf_counter() - start_t) * 1000)
                if r.status_code == 200:
                    models = [m.get("name") for m in r.json().get("models", []) if "name" in m]
                    return {
                        "status": "ok",
                        "latency_ms": latency_ms,
                        "models_count": len(models),
                        "models": models,
                        "detail": f"Ollama Server erreichbar ({latency_ms} ms, {len(models)} Modelle)"
                    }
                else:
                    return {"status": "error", "detail": f"Ollama HTTP {r.status_code}: {r.text[:150]}"}
            else:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                r = await client.get(f"{target_url}/models", headers=headers)
                latency_ms = int((time.perf_counter() - start_t) * 1000)
                if r.status_code == 200:
                    data = r.json()
                    models_count = len(data.get("data", []))
                    return {
                        "status": "ok",
                        "latency_ms": latency_ms,
                        "models_count": models_count,
                        "detail": f"API Endpoint erreichbar ({latency_ms} ms)"
                    }
                elif r.status_code in (401, 403):
                    return {"status": "error", "detail": "Ungültiger API-Key oder Zugriff verweigert (HTTP 401/403)"}
                else:
                    return {"status": "error", "detail": f"HTTP {r.status_code}: {r.text[:150]}"}
    except Exception as exc:
        return {"status": "error", "detail": f"Verbindungsfehler: {str(exc)}"}


async def fetch_available_models(provider: str, base_url: str = "", api_key: str = "") -> List[str]:
    """Fetches available LLM model names dynamically from the provider/endpoint."""
    models: List[str] = []

    if provider == "ollama":
        target_url = (base_url or "http://localhost:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{target_url}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    for item in data.get("models", []):
                        if "name" in item:
                            models.append(item["name"])
        except Exception as exc:
            logger.warning("Could not query Ollama tags from %s: %s", target_url, exc)
        
        if not models:
            models = ["llama3:8b", "gemma2:9b", "mistral:latest", "qwen2.5:32b"]

    elif provider == "openai":
        if api_key:
            target_url = (base_url or "https://api.openai.com/v1").rstrip("/")
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.get(f"{target_url}/models", headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        for item in data.get("data", []):
                            m_id = item.get("id")
                            if m_id:
                                models.append(str(m_id).strip())
            except Exception as exc:
                logger.warning("Could not query OpenAI models: %s", exc)

        if not models:
            models = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1-mini"]

    models = sorted(list(set(models)))
    return models


class LLMEndpointService:
    """Verwaltetes CRUD für Tenant-basierte LLM-Endpoints."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_endpoints(self, user_id: uuid.UUID) -> Sequence[UserLLMEndpoint]:
        res = await self.db.execute(
            sa_select(UserLLMEndpoint).where(UserLLMEndpoint.user_id == user_id)
            .order_by(UserLLMEndpoint.is_default.desc(), UserLLMEndpoint.created_at),
        )
        return res.scalars().all()

    async def _enforce_single_default(self, user_id: uuid.UUID):
        res = await self.db.execute(
            sa_select(UserLLMEndpoint).where(UserLLMEndpoint.user_id == user_id),
        )
        for ep in res.scalars().all():
            ep.is_default = False

    async def create_endpoint(
        self,
        user_id: uuid.UUID,
        provider: str,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> UserLLMEndpoint:
        if provider not in ("openai", "ollama", "custom"):
            raise ValueError("provider must be 'openai', 'ollama', or 'custom'")

        await self._enforce_single_default(user_id)

        ep = UserLLMEndpoint(
            id=uuid.uuid4(),
            user_id=user_id,
            provider=provider,
            base_url=base_url.strip()[:512] if base_url else None,
            api_key_encrypted=api_key,
            model=model.strip()[:128] if model else None,
            is_default=True,
        )
        self.db.add(ep)
        await self.db.flush()
        return ep

    async def get_one(self, endpoint_id: uuid.UUID, user_id: uuid.UUID) -> UserLLMEndpoint | None:
        res = await self.db.execute(
            sa_select(UserLLMEndpoint).where(
                UserLLMEndpoint.id == endpoint_id,
                UserLLMEndpoint.user_id == user_id,
            )
        )
        return res.scalar_one_or_none()

    async def get_default(self, user_id: uuid.UUID) -> UserLLMEndpoint | None:
        res = await self.db.execute(
            sa_select(UserLLMEndpoint).where(
                UserLLMEndpoint.user_id == user_id,
                UserLLMEndpoint.is_default == True,
            )
        )
        return res.scalar_one_or_none()

    async def delete_endpoint(self, endpoint_id: uuid.UUID, user_id: uuid.UUID):
        ep = await self.get_one(endpoint_id, user_id)
        if not ep:
            raise ValueError(f"Endpoint {endpoint_id} not found")
        await self.db.delete(ep)
        await self.db.flush()
