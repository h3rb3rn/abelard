"""V2 API router — multi-tenant Sessions & Knowledge pro User.

Jede Query filtert nach current_user.id → Mandanten-Trennung!
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func, or_ as sa_or, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import Agent, DebateSession, KVStore, User, UserLLMEndpoint, Project, ProjectDocument
from config import settings
from services import document_service
from services.agent_selection_service import AgentCandidate, select_agents_for_motion
from services.agent_transfer_service import (
    ImportResult,
    ImportValidationError,
    build_bundle,
    parse_bundle,
)
from services.document_service import UploadValidationError, get_document_index
from services.llm_client import LLMClient
from services.user_service import (
    UserService,
    create_access_token,
    decode_access_token,
)
from services.llm_endpoint_service import LLMEndpointService
from services.deps import get_db, get_orchestrator, register_orchestrator
from engine.orchestrator import DebateOrchestrator, ModeratorConfig


logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2"])


# =================================================================== JWT Security helper

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v2/auth/login", auto_error=False)


async def _get_current_user(
    token_header: Optional[str] = Depends(oauth2_scheme),
    token_query: Optional[str] = Query(None, alias="token"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decodes JWT from Authorization header or ?token= query param → returns active user."""
    token = token_header or token_query
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing sub in token")

    try:
        user_id = uuid.UUID(user_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed user id in token")

    svc = UserService(db)
    user = await svc.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or deactivated")
    return user


async def _get_current_user_optional(
    token: Optional[str] = Depends(OAuth2PasswordBearer(tokenUrl="/api/v2/auth/login", auto_error=False)),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    if not token:
        return None
    try:
        return await _get_current_user(token, db)
    except Exception:
        return None


# =================================================================== Pydantic contracts

class UserRegister(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    is_active: bool
    is_admin: bool = False

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TenantSessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = ""
    agents: list[str] = []
    project_motion: str = ""


class SessionResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    agent_ids: list[str]
    motion: str
    status: str
    created_at: str

    model_config = {"from_attributes": True}


class SessionStatusPatch(BaseModel):
    status: str = Field(description="active or completed")


class KnowledgeCreate(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    value: dict[str, Any] = {}
    category: Optional[str] = None
    tags: list[str] = []


class KVItem(BaseModel):
    id: str
    tenant_id: str
    key: str
    value: dict[str, Any]
    category: Optional[str]
    tags: list[str]
    created_at: str

    model_config = {"from_attributes": True}


# =================================================================== Project Models (mit LLM-Endpoint-Link)

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    motion: str
    agent_ids: list[str] = []
    moderator_goal: str = ""
    moderator_interval: int = Field(default=3, ge=1)
    max_rounds: int = Field(default=15, ge=1, le=500)
    max_duration_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    llm_endpoint_id: Optional[str] = None
    agent_selection_mode: str = Field(default="manual", pattern="^(manual|auto)$")
    auto_agent_count: int = Field(default=4, ge=2, le=60)


class ProjectRead(BaseModel):
    id: str
    name: str
    motion: str
    status: str
    agent_ids: list[str] = []
    moderator_config: Optional[dict[str, Any]] = None
    llm_endpoint_id: Optional[str] = None
    agent_selection_mode: str = "manual"
    auto_agent_count: int = 4
    created_at: str

    model_config = {"from_attributes": True}


# =================================================================== Helpers

def _kv_to_item(row: KVStore) -> KVItem:
    meta = row.meta_json or {}
    return KVItem(
        id=str(row.id),
        tenant_id=str(row.user_id),
        key=row.key,
        value=row.value_json or {},
        category=meta.get("category"),
        tags=meta.get("tags") or [],
        created_at=str(row.created_at) if row.created_at else "",
    )


def _uuid_or_400(value: str, label: str = "id"):
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(400, detail=f"Invalid {label}: {value}")


# =================================================================== 1. Auth endpoints (kein Token nötig)

@router.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegister, db: AsyncSession = Depends(get_db)):
    """Registriert einen neuen Tenant-User."""
    svc = UserService(db)
    exists = await svc.get_by_email(req.email)
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await svc.register(email=req.email, password=req.password, name=req.name)
    return UserResponse(
        id=str(user.id), email=user.email, name=user.name or "", is_active=True,
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Login und gibt JWT-Ticket zurück."""
    user = await UserService(db).login(email=form_data.username, password=form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


@router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(_get_current_user)):
    """Aktueller User aus JWT."""
    return UserResponse(
        id=str(current_user.id), email=current_user.email,
        name=current_user.name or "", is_active=current_user.is_active,
        is_admin=bool(current_user.is_admin),
    )


# =================================================================== 2. Tenant-Session Management

@router.post("/tenant/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant_session(
    req: TenantSessionCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Erstellt eine neue Tenant-Session für den aktuellen User."""

    # ---- Agent IDs prüfen (nur Agenten DES AKTUELLEN Users!) -----
    agent_ids: list[str] = []
    if req.agents:
        for aid_str in req.agents:
            aid = _uuid_or_400(aid_str, label="agent id")

            # MANDANTEN-SCOPING: user_id + current_user.id!
            res = await db.execute(
                sa_select(Agent).where(Agent.id == aid, Agent.user_id == current_user.id),
            )
            if not res.scalar_one_or_none():
                raise HTTPException(status_code=403, detail=f"Agent {aid_str} does not belong to you")
            agent_ids.append(str(aid))

    # ---- Session erstellen (im tenant store) --#
    session_obj = DebateSession(
        id=uuid.uuid4(),
        user_id=current_user.id,           # MANDANTEN-SCOPING!
        motion=req.project_motion or "",
        status="draft",
    )
    db.add(session_obj)
    await db.flush()

    return SessionResponse(
        id=str(session_obj.id), tenant_id=str(current_user.id),
        name=req.name[:256], description=req.description,
        agent_ids=agent_ids, motion=session_obj.motion or "",
        status="draft", created_at=str(session_obj.created_at) if session_obj.created_at else "",
    )


@router.get("/tenant/sessions", response_model=list[SessionResponse])
async def list_tenant_sessions(
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """NUR Sessions des autorisierten Users — Mandanten-Trennung!"""
    res = await db.execute(
        sa_select(DebateSession)
        .where(DebateSession.user_id == current_user.id)   # MANDANTEN-SCOPING!
        .order_by(DebateSession.created_at.desc()),
    )
    rows: list[DebateSession] = list(res.scalars().all())

    return [
        SessionResponse(
            id=str(s.id), tenant_id=str(current_user.id),
            name=(s.json_log_path or "Tenant-Sessions"),
            description=s.motion or "", agent_ids=[],
            motion=s.motion or "", status=s.status or "draft",
            created_at=str(s.created_at) if getattr(s, "created_at", None) else "",
        )
        for s in rows
    ]


@router.patch("/tenant/sessions/{session_id}/status", response_model=SessionResponse)
async def patch_session_status(
    session_id: str,
    req: SessionStatusPatch,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.status not in ("active", "completed"):
        raise HTTPException(400, detail="status must be 'active' or 'completed'")

    sid = _uuid_or_400(session_id)

    # MANDANTEN-SCOPING: user_id + session_id
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,   # MANDANTEN-SCOPING!
        ),
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, detail="Session not found")

    session.status = req.status
    await db.flush()
    await db.refresh(session)

    return SessionResponse(
        id=str(session.id), tenant_id=str(current_user.id),
        name=(session.json_log_path or "Tenant-Sessions"),
        description=session.motion or "", agent_ids=[],
        motion=session.motion or "", status=session.status,
        created_at=str(session.created_at) if getattr(session, "created_at", None) else "",
    )


# =================================================================== 3. User Knowledge / Context Store

@router.post("/knowledge", response_model=KVItem, status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    req: KnowledgeCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Speichert ein Wissen-Element im mandantensicheren kv_store."""
    entry = KVStore(
        id=uuid.uuid4(), user_id=current_user.id,  # MANDANTEN-SCOPING!
        key=req.key.strip()[:256], value_json=req.value,
        meta_json={"category": req.category, "tags": req.tags or []},
    )
    db.add(entry)
    await db.flush()

    return KVItem(
        id=str(entry.id), tenant_id=str(entry.user_id),
        key=entry.key, value=entry.value_json or {},
        category=(entry.meta_json or {}).get("category"),
        tags=(entry.meta_json or {}).get("tags") or [],
        created_at=str(entry.created_at) if entry.created_at else "",
    )


@router.get("/knowledge", response_model=list[KVItem])
async def list_knowledge(
    category: Optional[str] = Query(default=None),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Knowledge-Items nur des aktuell authentifizierten Users — Mandanten-Trennung!"""
    query = sa_select(KVStore).where(KVStore.user_id == current_user.id)

    if category is not None:
        res_raw = await db.execute(query.order_by(KVStore.created_at.desc()))
        rows: list[KVStore] = list(res_raw.scalars().all())
        return [r for r in rows if (r.meta_json or {}).get("category") == category]

    res = await db.execute(query.order_by(KVStore.created_at.desc()))
    return [_kv_to_item(r) for r in res.scalars().all()]


@router.patch("/knowledge/{item_id}", response_model=KVItem)
async def update_knowledge(
    item_id: str,
    req: KnowledgeCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aktualisiert ein Wissen-Element im kv_store."""
    iid = _uuid_or_400(item_id)
    res = await db.execute(
        sa_select(KVStore).where(
            KVStore.id == iid, KVStore.user_id == current_user.id
        )
    )
    entry = res.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, detail="Knowledge item not found")

    if req.key:
        entry.key = req.key.strip()[:256]
    if req.value is not None:
        entry.value_json = req.value
    db.add(entry)
    await db.flush()

    return KVItem(
        id=str(entry.id), tenant_id=str(entry.user_id),
        key=entry.key, value=entry.value_json or {},
        category=(entry.meta_json or {}).get("category"),
        tags=(entry.meta_json or {}).get("tags") or [],
        created_at=str(entry.created_at) if entry.created_at else "",
    )


@router.delete("/knowledge/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(
    item_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Löscht ein Wissen-Element. Nur der Besitzer kann es löschen.

    MANDANTEN-SCOPING: Immer user_id + id — kein Cross-Tenant-Zugriff möglich!
    """
    iid = _uuid_or_400(item_id)

    res = await db.execute(
        sa_select(KVStore).where(
            KVStore.id == iid, KVStore.user_id == current_user.id,   # MANDANTEN-SCOPING!
        ),
    )
    entry = res.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, detail="Knowledge item not found")

    await db.delete(entry)
    await db.flush()


# =================================================================== 4. User LLM-Endpoint Management (CRUD + set-default)

class LLMEndpointCreate(BaseModel):
    provider: str = Field(description="openai oder ollama")
    base_url: str = ""
    api_key: Optional[str] = None
    llm_model: Optional[str] = None


class LLMEndpointUpdate(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    llm_model: Optional[str] = None


class LLMEndpointResponse(BaseModel):
    id: str
    user_id: str
    provider: str
    base_url: str | None
    api_key_encrypted: str | None
    model: str | None
    is_default: bool
    created_at: str

    model_config = {"from_attributes": True}


from services.llm_endpoint_service import LLMEndpointService, fetch_available_models, test_endpoint_connection


@router.get("/llm-endpoints/models", response_model=list[str])
async def get_available_models(
    provider: str = "openai",
    base_url: str = "",
    api_key: str = "",
    current_user: Optional[User] = Depends(_get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Dynamisch verfügbare LLM-Modellnamen vom Provider/Server abrufen."""
    if current_user and (not api_key or not base_url):
        svc = LLMEndpointService(db)
        endpoints = await svc.list_endpoints(current_user.id)
        ep_matches = [e for e in endpoints if e.provider == provider]
        target_ep = ep_matches[0] if ep_matches else (endpoints[0] if endpoints else None)
        if target_ep:
            if not base_url and target_ep.base_url:
                base_url = target_ep.base_url
            if not api_key and target_ep.api_key_encrypted:
                api_key = target_ep.api_key_encrypted
            if not provider:
                provider = target_ep.provider

    return await fetch_available_models(provider=provider, base_url=base_url, api_key=api_key)


@router.post("/llm-endpoints/test")
async def test_llm_connection_params(req: LLMEndpointCreate):
    """Prüft, ob eine LLM-Verbindung mit den angegebenen Daten erreichbar ist."""
    return await test_endpoint_connection(provider=req.provider, base_url=req.base_url, api_key=req.api_key or "")


@router.post("/llm-endpoints/{endpoint_id}/test")
async def test_saved_llm_endpoint(
    endpoint_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Prüft die Erreichbarkeit eines gespeicherten Endpoints."""
    eid = _uuid_or_400(endpoint_id)
    svc = LLMEndpointService(db)
    ep = await svc.get_one(eid, current_user.id)
    if not ep:
        raise HTTPException(404, detail="Endpoint nicht gefunden")
    return await test_endpoint_connection(provider=ep.provider, base_url=ep.base_url or "", api_key=ep.api_key_encrypted or "")


@router.get("/llm-endpoints/{endpoint_id}/models", response_model=list[str])
async def get_saved_endpoint_models(
    endpoint_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fragt verfügbare Modelle für einen gespeicherten Endpoint ab."""
    eid = _uuid_or_400(endpoint_id)
    svc = LLMEndpointService(db)
    ep = await svc.get_one(eid, current_user.id)
    if not ep:
        raise HTTPException(404, detail="Endpoint nicht gefunden")
    return await fetch_available_models(provider=ep.provider, base_url=ep.base_url or "", api_key=ep.api_key_encrypted or "")


@router.post("/llm-endpoints", response_model=LLMEndpointResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_endpoint(
    req: LLMEndpointCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Erstellt einen neuen LLM-Endpoint."""
    svc = LLMEndpointService(db)
    try:
        ep = await svc.create_endpoint(
            user_id=current_user.id,
            provider=req.provider,
            base_url=req.base_url,
            api_key=req.api_key or "",
            model=req.llm_model or "",
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

    return LLMEndpointResponse(
        id=str(ep.id),
        user_id=str(ep.user_id),
        provider=ep.provider,
        base_url=ep.base_url,
        api_key_encrypted=ep.api_key_encrypted,
        model=ep.model,
        is_default=ep.is_default,
        created_at=str(ep.created_at) if ep.created_at else "",
    )


@router.get("/llm-endpoints", response_model=list[LLMEndpointResponse])
async def list_llm_endpoints(
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Listet alle LLM-Endpoints des aktuellen Users, sortiert nach Default + created_at."""
    res = await db.execute(
        sa_select(UserLLMEndpoint)
        .where(UserLLMEndpoint.user_id == current_user.id)
        .order_by(UserLLMEndpoint.is_default.desc(), UserLLMEndpoint.created_at.desc()),
    )
    endpoints: list[UserLLMEndpoint] = list(res.scalars().all())

    return [
        LLMEndpointResponse(
            id=str(ep.id),
            user_id=str(ep.user_id),
            provider=ep.provider,
            base_url=ep.base_url,
            api_key_encrypted=ep.api_key_encrypted,
            model=ep.model,
            is_default=ep.is_default,
            created_at=str(ep.created_at) if ep.created_at else "",
        )
        for ep in endpoints
    ]


@router.get("/llm-endpoints/{endpoint_id}", response_model=LLMEndpointResponse)
async def get_llm_endpoint(
    endpoint_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Holt einen spezifischen LLM-Endpoint des aktuellen Users."""
    eid = _uuid_or_400(endpoint_id)

    res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.id == eid,
            UserLLMEndpoint.user_id == current_user.id,
        ),
    )
    ep = res.scalar_one_or_none()
    if not ep:
        raise HTTPException(404, detail="Endpoint nicht gefunden oder Zugriff verweigert")

    return LLMEndpointResponse(
        id=str(ep.id),
        user_id=str(ep.user_id),
        provider=ep.provider,
        base_url=ep.base_url,
        api_key_encrypted=ep.api_key_encrypted,
        model=ep.model,
        is_default=ep.is_default,
        created_at=str(ep.created_at) if ep.created_at else "",
    )


@router.patch("/llm-endpoints/{endpoint_id}", response_model=LLMEndpointResponse)
async def update_llm_endpoint(
    endpoint_id: str,
    req: LLMEndpointUpdate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aktualisiert einen vorhandenen LLM-Endpoint."""
    eid = _uuid_or_400(endpoint_id)

    res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.id == eid,
            UserLLMEndpoint.user_id == current_user.id,
        ),
    )
    ep = res.scalar_one_or_none()
    if not ep:
        raise HTTPException(404, detail="Endpoint nicht gefunden oder Zugriff verweigert")

    if req.provider is not None:
        if req.provider not in ("openai", "ollama"):
            raise HTTPException(400, detail="provider muss 'openai' oder 'ollama' sein")
        ep.provider = req.provider

    if req.base_url is not None:
        ep.base_url = req.base_url.strip()[:512] if req.base_url else None

    if req.api_key is not None:
        ep.api_key_encrypted = req.api_key

    if req.llm_model is not None:
        ep.model = req.llm_model.strip()[:128] if req.llm_model else None

    await db.flush()
    await db.refresh(ep)

    return LLMEndpointResponse(
        id=str(ep.id),
        user_id=str(ep.user_id),
        provider=ep.provider,
        base_url=ep.base_url,
        api_key_encrypted=ep.api_key_encrypted,
        model=ep.model,
        is_default=ep.is_default,
        created_at=str(ep.created_at) if ep.created_at else "",
    )


@router.post("/llm-endpoints/{endpoint_id}/set-default", response_model=LLMEndpointResponse)
async def set_llm_endpoint_default(
    endpoint_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Setzt einen bestimmten Endpoint als Default und alle anderen des Users auf False."""
    eid = _uuid_or_400(endpoint_id)

    # Prüfen ob der Endpoint dem User gehört
    res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.id == eid,
            UserLLMEndpoint.user_id == current_user.id,
        ),
    )
    ep = res.scalar_one_or_none()
    if not ep:
        raise HTTPException(404, detail="Endpoint nicht gefunden oder Zugriff verweigert")

    # Alle anderen Endpoints des Users auf is_default=False setzen
    res2 = await db.execute(
        sa_select(UserLLMEndpoint).where(UserLLMEndpoint.user_id == current_user.id),
    )
    for other_ep in res2.scalars().all():
        if other_ep.id != ep.id:
            other_ep.is_default = False

    ep.is_default = True
    await db.flush()
    await db.refresh(ep)

    return LLMEndpointResponse(
        id=str(ep.id),
        user_id=str(ep.user_id),
        provider=ep.provider,
        base_url=ep.base_url,
        api_key_encrypted=ep.api_key_encrypted,
        model=ep.model,
        is_default=ep.is_default,
        created_at=str(ep.created_at) if ep.created_at else "",
    )


@router.delete("/llm-endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_endpoint(
    endpoint_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Löscht einen LLM-Endpoint. Nur der Besitzer kann ihn löschen."""
    eid = _uuid_or_400(endpoint_id)

    res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.id == eid,
            UserLLMEndpoint.user_id == current_user.id,
        ),
    )
    ep = res.scalar_one_or_none()
    if not ep:
        raise HTTPException(404, detail="Endpoint nicht gefunden oder Zugriff verweigert")

    await db.delete(ep)
    await db.flush()


# =================================================================== 5. Projekt CRUD (mit LLM-Endpoint-Auswahl pro User & Agent-Zuordnung)

@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    req: ProjectCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Erstellt ein Projekt mit Motion, zugewiesenen Agenten und Moderator-Ziel."""
    llm_endpoint_id = None
    if req.llm_endpoint_id:
        res = await db.execute(
            sa_select(UserLLMEndpoint).where(
                UserLLMEndpoint.id == uuid.UUID(req.llm_endpoint_id),
                UserLLMEndpoint.user_id == current_user.id,
            )
        )
        ep = res.scalar_one_or_none()
        if not ep:
            raise HTTPException(404, "LLM endpoint nicht gefunden oder gehört nicht dir")
        llm_endpoint_id = uuid.UUID(req.llm_endpoint_id)

    mod_cfg = {
        "goal": req.moderator_goal or "Zielorientierte Synthese und Konsensfindung.",
        "interval_turns": req.moderator_interval or 3,
        "max_rounds": req.max_rounds or 15,
        "max_duration_minutes": req.max_duration_minutes,
    }

    project = Project(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=req.name.strip()[:256],
        motion=req.motion,
        status="draft",
        moderator_config=mod_cfg,
        user_llm_endpoint_id=llm_endpoint_id,
        agent_selection_mode=req.agent_selection_mode,
        auto_agent_count=req.auto_agent_count,
    )
    db.add(project)
    await db.flush()

    assigned_agent_ids = await _assign_agents_to_project(req.agent_ids, project, current_user, db)

    return ProjectRead(
        id=str(project.id),
        name=project.name,
        motion=project.motion,
        status=project.status,
        agent_ids=assigned_agent_ids,
        moderator_config=project.moderator_config,
        llm_endpoint_id=str(project.user_llm_endpoint_id) if project.user_llm_endpoint_id else None,
        agent_selection_mode=(project.agent_selection_mode or "manual"),
        auto_agent_count=int(project.auto_agent_count or 4),
        created_at=str(project.created_at) if project.created_at else "",
    )


@router.get("/projects", response_model=list[ProjectRead])
async def list_projects(
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Nur Projekte des aktuellen Users — Mandanten-Trennung!"""
    res = await db.execute(
        sa_select(Project).where(Project.user_id == current_user.id)
        .order_by(Project.created_at.desc()),
    )
    projects = list(res.scalars().all())

    result: list[ProjectRead] = []
    for p in projects:
        res_ag = await db.execute(
            sa_select(Agent.id).where(Agent.project_id == p.id, Agent.user_id == current_user.id)
        )
        ag_ids = [str(aid) for aid in res_ag.scalars().all()]
        result.append(
            ProjectRead(
                id=str(p.id),
                name=p.name,
                motion=(p.motion or ""),
                status=(p.status or "draft"),
                agent_ids=ag_ids,
                moderator_config=p.moderator_config,
                llm_endpoint_id=str(p.user_llm_endpoint_id) if p.user_llm_endpoint_id else None,
                created_at=str(p.created_at) if p.created_at else "",
            )
        )
    return result


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Einzelnes Projekt — nur der Owner kann es lesen."""
    pid = _uuid_or_400(project_id)

    res = await db.execute(
        sa_select(Project).where(
            Project.id == pid,
            Project.user_id == current_user.id,
        ),
    )
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(404, detail="Projekt nicht gefunden")

    res_ag = await db.execute(
        sa_select(Agent.id).where(Agent.project_id == project.id, Agent.user_id == current_user.id)
    )
    ag_ids = [str(aid) for aid in res_ag.scalars().all()]

    return ProjectRead(
        id=str(project.id),
        name=project.name,
        motion=(project.motion or ""),
        status=(project.status or "draft"),
        agent_ids=ag_ids,
        moderator_config=project.moderator_config,
        llm_endpoint_id=str(project.user_llm_endpoint_id) if project.user_llm_endpoint_id else None,
        agent_selection_mode=(project.agent_selection_mode or "manual"),
        auto_agent_count=int(project.auto_agent_count or 4),
        created_at=str(project.created_at) if project.created_at else "",
    )


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    motion: Optional[str] = None
    agent_ids: Optional[list[str]] = None
    moderator_goal: Optional[str] = None
    moderator_interval: Optional[int] = Field(default=None, ge=1)
    max_rounds: Optional[int] = Field(default=None, ge=1, le=500)
    max_duration_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    llm_endpoint_id: Optional[str] = None
    agent_selection_mode: Optional[str] = Field(default=None, pattern="^(manual|auto)$")
    auto_agent_count: Optional[int] = Field(default=None, ge=2, le=60)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: str,
    req: ProjectUpdate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pid = _uuid_or_400(project_id)

    res = await db.execute(
        sa_select(Project).where(
            Project.id == pid,
            Project.user_id == current_user.id,
        ),
    )
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(404, detail="Projekt nicht gefunden")

    if req.name is not None:
        project.name = req.name.strip()[:256]
    if req.motion is not None:
        project.motion = req.motion

    mod_cfg = dict(project.moderator_config or {})
    if req.moderator_goal is not None:
        mod_cfg["goal"] = req.moderator_goal
    if req.moderator_interval is not None:
        mod_cfg["interval_turns"] = req.moderator_interval
    if req.max_rounds is not None:
        mod_cfg["max_rounds"] = req.max_rounds
    if req.max_duration_minutes is not None:
        mod_cfg["max_duration_minutes"] = req.max_duration_minutes
    project.moderator_config = mod_cfg

    if req.agent_selection_mode is not None:
        project.agent_selection_mode = req.agent_selection_mode
    if req.auto_agent_count is not None:
        project.auto_agent_count = req.auto_agent_count

    if req.agent_ids is not None:
        await _assign_agents_to_project(req.agent_ids, project, current_user, db)

    if req.llm_endpoint_id is not None:
        if req.llm_endpoint_id == "":
            project.user_llm_endpoint_id = None
        else:
            res2 = await db.execute(
                sa_select(UserLLMEndpoint).where(
                    UserLLMEndpoint.id == uuid.UUID(req.llm_endpoint_id),
                    UserLLMEndpoint.user_id == current_user.id,
                )
            )
            ep = res2.scalar_one_or_none()
            if not ep:
                raise HTTPException(404, "LLM endpoint nicht gefunden oder gehört nicht dir")
            project.user_llm_endpoint_id = uuid.UUID(req.llm_endpoint_id)

    await db.flush()
    await db.refresh(project)

    res_ag = await db.execute(
        sa_select(Agent.id).where(Agent.project_id == project.id, Agent.user_id == current_user.id)
    )
    ag_ids = [str(aid) for aid in res_ag.scalars().all()]

    return ProjectRead(
        id=str(project.id),
        name=project.name,
        motion=(project.motion or ""),
        status=(project.status or "draft"),
        agent_ids=ag_ids,
        moderator_config=project.moderator_config,
        llm_endpoint_id=str(project.user_llm_endpoint_id) if project.user_llm_endpoint_id else None,
        agent_selection_mode=(project.agent_selection_mode or "manual"),
        auto_agent_count=int(project.auto_agent_count or 4),
        created_at=str(project.created_at) if project.created_at else "",
    )


async def _run_auto_selection(
    project: Project, current_user: User, db: AsyncSession, count: int | None = None
) -> tuple[list, str, dict[str, Agent]]:
    """Fuehrt die KI-gestuetzte Themenanalyse und Agentenauswahl aus."""
    agents = await _collect_agent_candidates(current_user, db, project.id)
    if not agents:
        raise HTTPException(status_code=400, detail="Keine Agenten verfuegbar — lege welche an oder nutze die Persona-Bibliothek")

    ep = await _user_default_endpoint(current_user.id, db)
    fallback_model = next((a.llm_model for a in agents if a.llm_model), None)
    client = _selection_llm_client(ep, fallback_model)
    if client is None:
        logger.warning("Kein Profil-LLM fuer die Auswahl bei User %s — Heuristik greift", current_user.email)

    picks, rationale = await select_agents_for_motion(
        motion=project.motion,
        candidates=_to_candidates(agents),
        llm_client=client,
        count=count or project.auto_agent_count or 4,
    )
    return picks, rationale, {str(a.id): a for a in agents}


@router.post("/projects/{project_id}/suggest-agents")
async def suggest_agents_for_project(
    project_id: str,
    count: Optional[int] = Query(None, ge=2, le=60),
    apply: bool = Query(False, description="Auswahl direkt als Projekt-Agenten anlegen"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Schlaegt per LLM die zum Thema passendsten Agenten vor (Vorschau oder direkt anwenden).

    Die Analyse laeuft ueber den im Profil favorisierten LLM-Endpoint.
    """
    project = await _get_owned_project(project_id, current_user, db)
    picks, rationale, by_id = await _run_auto_selection(project, current_user, db, count)

    applied: list[str] = []
    if apply:
        created = await _materialize_auto_agents(picks, by_id, project, current_user, db)
        applied = [str(a.id) for a in created]

    return {
        "project_id": str(project.id),
        "motion": project.motion,
        "rationale": rationale,
        "applied": bool(apply),
        "agent_ids": applied,
        "selection": [
            {
                "name": p.candidate.name,
                "source_agent_id": p.candidate.id,
                "reason": p.reason,
                "is_global": bool(by_id[p.candidate.id].is_global) if p.candidate.id in by_id else False,
            }
            for p in picks
        ],
    }


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pid = _uuid_or_400(project_id)

    res = await db.execute(
        sa_select(Project).where(
            Project.id == pid,
            Project.user_id == current_user.id,
        ),
    )
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(404, detail="Projekt nicht gefunden oder Zugriff verweigert")

    await db.delete(project)
    await db.flush()


# =================================================================== 6. Agent CRUD (pro User, optional Projekt-Bindung für Debate)

from services.search_service import perform_web_search


class WebSearchRequest(BaseModel):
    query: str
    provider: str = "duckduckgo"
    searxng_url: Optional[str] = None
    max_results: int = 5


@router.post("/tools/web-search")
async def execute_web_search_route(
    req: WebSearchRequest,
    current_user: User = Depends(_get_current_user),
):
    """Führt eine Online-Websuche via DuckDuckGo oder SearXNG durch."""
    return await perform_web_search(
        query=req.query,
        provider=req.provider,
        searxng_url=req.searxng_url or "",
        max_results=req.max_results,
    )


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str = ""
    persona_bio: Optional[str] = None
    project_id: Optional[str] = None
    llm_provider: str = "openai"
    llm_model: Optional[str] = None
    web_search_enabled: bool = False
    web_search_provider: Optional[str] = "duckduckgo"
    searxng_url: Optional[str] = None


class AgentRead(BaseModel):
    id: str
    name: str
    system_prompt: str
    persona_bio: Optional[str] = None
    llm_provider: str
    llm_model: Optional[str]
    web_search_enabled: bool = False
    web_search_provider: Optional[str] = "duckduckgo"
    searxng_url: Optional[str] = None
    is_global: bool = False
    is_owner: bool = True  # False = global freigegebener Agent eines anderen Users
    created_at: str

    model_config = {"from_attributes": True}


class AgentGlobalPatch(BaseModel):
    is_global: bool


def _agent_to_read(agent: Agent, current_user_id: uuid.UUID) -> AgentRead:
    return AgentRead(
        id=str(agent.id),
        name=(agent.name or ""),
        system_prompt=(agent.system_prompt or ""),
        persona_bio=agent.persona_bio,
        llm_provider=(agent.llm_provider or "openai"),
        llm_model=agent.llm_model,
        web_search_enabled=bool(agent.web_search_enabled),
        web_search_provider=agent.web_search_provider or "duckduckgo",
        searxng_url=agent.searxng_url,
        is_global=bool(agent.is_global),
        is_owner=(agent.user_id == current_user_id),
        created_at=str(agent.created_at) if agent.created_at else "",
    )


async def _get_agent_owned_or_global(agent_id: uuid.UUID, current_user: User, db: AsyncSession) -> Agent:
    """Agent laden, wenn er dem User gehoert ODER global freigegeben ist."""
    res = await db.execute(
        sa_select(Agent).where(
            Agent.id == agent_id,
            sa_or(Agent.user_id == current_user.id, Agent.is_global == True),  # noqa: E712
        )
    )
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    return agent


AUTO_MARKER = "auto_assigned"
DEFAULT_AGENT_MODEL = "gpt-4o-mini"
DEFAULT_AGENT_TEMPERATURE = 0.7
AGENT_WEB_SEARCH_RESULTS = 5


async def _build_agent_config(
    agent: Agent,
    motion: str,
    ep_map: dict[str, UserLLMEndpoint],
    default_ep: Optional[UserLLMEndpoint],
) -> dict[str, Any]:
    """Baut die Orchestrator-Konfiguration eines Agenten.

    Enthaelt Persona-Biografie, optionale Websuche zum Thema und die Aufloesung
    des LLM-Endpoints (agenteigene Werte schlagen den Projekt-/Default-Endpoint).
    """
    system_prompt = agent.system_prompt or f"You are {agent.name}, a debate participant."
    if agent.persona_bio and agent.persona_bio.strip():
        system_prompt = (
            f"### PERSÖNLICHKEIT & BIOGRAFIE ({agent.name}):\n{agent.persona_bio.strip()}\n\n"
            f"### AUFGABE & SYSTEM PROMPT:\n{system_prompt}"
        )

    if agent.web_search_enabled:
        try:
            search_res = await perform_web_search(
                query=motion,
                provider=agent.web_search_provider or "duckduckgo",
                searxng_url=agent.searxng_url or "",
                max_results=AGENT_WEB_SEARCH_RESULTS,
            )
            system_prompt += f"\n\n{search_res['formatted_text']}"
        except Exception as exc:
            logger.warning("Websuche fuer Agent %s fehlgeschlagen: %s", agent.name, exc)

    target_ep = ep_map.get(agent.llm_provider) or default_ep
    return {
        "provider": agent.llm_provider or "openai",
        "model": agent.llm_model or DEFAULT_AGENT_MODEL,
        "base_url": agent.llm_base_url or (target_ep.base_url if target_ep else ""),
        "api_key": (target_ep.api_key_encrypted if target_ep else "") or "",
        "temperature": agent.temperature or DEFAULT_AGENT_TEMPERATURE,
        "system_prompt": system_prompt,
    }


async def _user_default_endpoint(user_id: uuid.UUID, db: AsyncSession) -> Optional[UserLLMEndpoint]:
    """Der im Profil favorisierte LLM-Endpoint des Users."""
    res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.user_id == user_id,
            UserLLMEndpoint.is_default == True,  # noqa: E712
        )
    )
    ep = res.scalars().first()
    if ep:
        return ep
    res_any = await db.execute(
        sa_select(UserLLMEndpoint).where(UserLLMEndpoint.user_id == user_id).order_by(UserLLMEndpoint.created_at)
    )
    return res_any.scalars().first()


def _selection_llm_client(ep: Optional[UserLLMEndpoint], fallback_model: Optional[str]) -> Optional[LLMClient]:
    """Baut den LLM-Client fuer die Themenanalyse aus dem Profil-Endpoint."""
    if not ep:
        return None
    model = ep.model or fallback_model
    if not model:
        return None
    return LLMClient(
        provider=(ep.provider or "openai"),
        model=model,
        base_url=ep.base_url or "",
        api_key=ep.api_key_encrypted or "",
        temperature=0.2,  # Auswahl soll reproduzierbar sein, nicht kreativ
    )


async def _collect_agent_candidates(
    current_user: User, db: AsyncSession, project_id: uuid.UUID | None
) -> list[Agent]:
    """Alle waehlbaren Agenten: eigene plus global freigegebene.

    Bereits automatisch angelegte Agenten desselben Projekts werden ausgeschlossen,
    damit wiederholte Laeufe nicht ihre eigenen Kopien erneut auswaehlen.
    """
    res = await db.execute(
        sa_select(Agent).where(
            sa_or(Agent.user_id == current_user.id, Agent.is_global == True),  # noqa: E712
        )
    )
    candidates: list[Agent] = []
    seen_names: set[str] = set()
    for a in res.scalars().all():
        if project_id and a.project_id == project_id and (a.skills_json or {}).get(AUTO_MARKER):
            continue
        if a.name in seen_names:  # eigene Kopie schlaegt globales Original
            continue
        seen_names.add(a.name)
        candidates.append(a)
    return candidates


def _to_candidates(agents: list[Agent]) -> list[AgentCandidate]:
    out = []
    for a in agents:
        bio = (a.persona_bio or a.system_prompt or "").strip()
        field = ""
        if a.persona_bio and "—" in a.persona_bio.split("\n", 1)[0]:
            field = a.persona_bio.split("\n", 1)[0].split("—", 1)[1].strip()
        out.append(AgentCandidate(id=str(a.id), name=a.name, field=field, bio=bio))
    return out


async def _materialize_auto_agents(
    picks: list, agents_by_id: dict[str, Agent], project: Project, current_user: User, db: AsyncSession
) -> list[Agent]:
    """Legt fuer die Auswahl Projekt-Agenten mit vollen Tools an.

    Es werden immer Kopien erzeugt: So bleiben Originale (auch globale fremde)
    unveraendert, und das Aktivieren aller Werkzeuge wirkt nur in diesem Projekt.
    """
    old = await db.execute(sa_select(Agent).where(Agent.project_id == project.id, Agent.user_id == current_user.id))
    for prev in old.scalars().all():
        if (prev.skills_json or {}).get(AUTO_MARKER):
            await db.delete(prev)
    await db.flush()

    created: list[Agent] = []
    for pick in picks:
        source = agents_by_id.get(pick.candidate.id)
        if not source:
            continue
        clone = _clone_agent_for_user(source, current_user.id, project_id=project.id)
        # Volle Werkzeug- und Online-Recherche-Rechte fuer automatisch gewaehlte Agenten
        clone.web_search_enabled = True
        clone.web_search_provider = source.web_search_provider or "searxng"
        clone.searxng_url = source.searxng_url or settings.searxng_base_url
        clone.knowledge_graph_enabled = True
        clone.cache_enabled = True
        clone.mcp_enabled = True
        clone.skills_json = {AUTO_MARKER: True, "selection_reason": pick.reason}
        db.add(clone)
        created.append(clone)
    await db.flush()
    return created


async def _assign_agents_to_project(
    agent_ids: list[str],
    project: Project,
    current_user: User,
    db: AsyncSession,
) -> list[str]:
    """Weist Agenten einem Projekt zu.

    Eigene Agenten werden direkt zugeordnet. Global freigegebene Agenten anderer User
    werden zuvor in den eigenen Mandanten geklont — sonst wuerde das Setzen von
    ``project_id`` den Agenten aus dem fremden Konto herausziehen.
    """
    assigned: list[str] = []
    if not agent_ids:
        return assigned

    for aid_str in agent_ids:
        try:
            aid = uuid.UUID(aid_str)
        except (ValueError, TypeError):
            continue

        res = await db.execute(
            sa_select(Agent).where(
                Agent.id == aid,
                sa_or(Agent.user_id == current_user.id, Agent.is_global == True),  # noqa: E712
            )
        )
        agent = res.scalar_one_or_none()
        if not agent:
            continue

        if agent.user_id == current_user.id:
            agent.project_id = project.id
            db.add(agent)
            assigned.append(str(agent.id))
            continue

        # Globaler Agent eines anderen Users → eigene Kopie im Projekt anlegen
        existing = await db.execute(
            sa_select(Agent).where(
                Agent.user_id == current_user.id,
                Agent.name == agent.name,
                Agent.project_id == project.id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        clone = _clone_agent_for_user(agent, current_user.id, project_id=project.id)
        db.add(clone)
        await db.flush()
        assigned.append(str(clone.id))

    await db.flush()
    return assigned


def _clone_agent_for_user(source: Agent, user_id: uuid.UUID, project_id: uuid.UUID | None = None) -> Agent:
    """Kopie eines (globalen) Agenten im Mandanten des Users — nie den Originaleintrag verschieben."""
    return Agent(
        id=uuid.uuid4(),
        user_id=user_id,
        project_id=project_id,
        name=source.name,
        system_prompt=source.system_prompt,
        persona_bio=source.persona_bio,
        llm_provider=source.llm_provider,
        llm_base_url=source.llm_base_url,
        llm_model=source.llm_model,
        temperature=source.temperature,
        skills_json=source.skills_json,
        knowledge_graph_enabled=source.knowledge_graph_enabled,
        cache_enabled=source.cache_enabled,
        mcp_enabled=source.mcp_enabled,
        web_search_enabled=source.web_search_enabled,
        web_search_provider=source.web_search_provider,
        searxng_url=source.searxng_url,
        is_global=False,  # Kopien sind privat
    )


@router.post("/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    req: AgentCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = None
    if req.project_id:
        try:
            project_id = uuid.UUID(req.project_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ungültige Projekt-ID")

    agent = Agent(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=req.name.strip()[:128],
        system_prompt=req.system_prompt if req.system_prompt else "Du bist ein hilfreicher Debatten-Assistent.",
        persona_bio=req.persona_bio.strip() if req.persona_bio else None,
        llm_provider=(req.llm_provider or "openai").strip(),
        llm_model=req.llm_model,
        web_search_enabled=req.web_search_enabled,
        web_search_provider=req.web_search_provider or "duckduckgo",
        searxng_url=req.searxng_url,
        project_id=project_id,
    )
    db.add(agent)
    await db.flush()

    return AgentRead(
        id=str(agent.id),
        name=agent.name,
        system_prompt=(agent.system_prompt or ""),
        persona_bio=agent.persona_bio,
        llm_provider=(agent.llm_provider or "openai"),
        llm_model=agent.llm_model,
        web_search_enabled=agent.web_search_enabled,
        web_search_provider=agent.web_search_provider,
        searxng_url=agent.searxng_url,
        created_at=str(agent.created_at) if agent.created_at else "",
    )


@router.get("/agents", response_model=list[AgentRead])
async def list_agents(
    scope: str = Query("all", pattern="^(all|own|global)$"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Listet Agenten: eigene plus global freigegebene anderer User.

    ``scope=own`` beschraenkt auf eigene, ``scope=global`` auf global freigegebene.
    """
    if scope == "own":
        condition = Agent.user_id == current_user.id
    elif scope == "global":
        condition = Agent.is_global == True  # noqa: E712
    else:
        condition = sa_or(Agent.user_id == current_user.id, Agent.is_global == True)  # noqa: E712

    res = await db.execute(sa_select(Agent).where(condition).order_by(Agent.created_at.desc()))
    return [_agent_to_read(a, current_user.id) for a in res.scalars().all()]


@router.post("/agents/seed-personas")
async def seed_persona_agents(
    make_global: bool = Query(False, description="Personas direkt global freigeben (nur Admins)"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legt die Persona-Bibliothek (50 Wissenschaftler:innen + fiktive KIs) fuer den User an.

    Idempotent: bereits vorhandene Agenten (Namensgleichheit) werden uebersprungen.
    Provider/Modell kommen vom Default-LLM-Endpoint des Users, falls vorhanden.
    """
    from services.persona_library import (
        FICTIONAL_PERSONAS,
        PERSONAS,
        build_persona_bio,
        build_system_prompt,
    )

    all_personas = PERSONAS + FICTIONAL_PERSONAS

    if make_global and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Nur Admins duerfen Agenten global freigeben")

    res = await db.execute(sa_select(Agent.name).where(Agent.user_id == current_user.id))
    existing_names = {row[0] for row in res.all()}

    ep_res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.user_id == current_user.id,
            UserLLMEndpoint.is_default == True,  # noqa: E712
        )
    )
    default_ep = ep_res.scalars().first()
    provider = (default_ep.provider if default_ep else "openai") or "openai"
    model = default_ep.model if default_ep else None

    created: list[str] = []
    for persona in all_personas:
        if persona["name"] in existing_names:
            continue
        db.add(Agent(
            id=uuid.uuid4(),
            user_id=current_user.id,
            name=persona["name"],
            system_prompt=build_system_prompt(persona),
            persona_bio=build_persona_bio(persona),
            llm_provider=provider,
            llm_model=model,
            is_global=make_global,
        ))
        created.append(persona["name"])

    await db.flush()
    logger.info(
        "Persona-Bibliothek fuer User %s: %d neu angelegt, %d uebersprungen",
        current_user.id, len(created), len(all_personas) - len(created),
    )
    return {
        "total": len(all_personas),
        "created": len(created),
        "skipped": len(all_personas) - len(created),
        "created_names": created,
    }


@router.get("/agents/export")
async def export_agents(
    scope: str = Query("own", pattern="^(all|own|global)$"),
    portable: bool = Query(True, description="Installationsspezifische Felder weglassen"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Exportiert Agenten als versioniertes JSON-Buendel.

    ``portable=true`` laesst Modell, Basis-URL und SearXNG-Adresse weg — diese Form
    ist zum Weitergeben und fuer Seed-Dateien gedacht. API-Schluessel sind nie
    enthalten, sie haengen am LLM-Endpoint und nicht am Agenten.
    """
    if scope == "own":
        condition = Agent.user_id == current_user.id
    elif scope == "global":
        condition = Agent.is_global == True  # noqa: E712
    else:
        condition = sa_or(Agent.user_id == current_user.id, Agent.is_global == True)  # noqa: E712

    res = await db.execute(sa_select(Agent).where(condition).order_by(Agent.name))

    # Automatisch erzeugte Projekt-Arbeitskopien sind Ableitungen, keine eigenen
    # Personas — sie wuerden den Export nur aufblaehen. Gleiche Namen werden
    # zusammengefasst, damit eine projektgebundene Kopie ihr Original nicht doppelt.
    agents: list[Agent] = []
    seen: set[str] = set()
    for a in res.scalars().all():
        if (a.skills_json or {}).get(AUTO_MARKER):
            continue
        if a.name in seen:
            continue
        seen.add(a.name)
        agents.append(a)

    bundle = build_bundle(agents, portable=portable, source=f"abelard/{scope}")

    filename = f"abelard-agents-{scope}{'-portable' if portable else ''}.json"
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _import_agent_entries(
    entries: list[dict[str, Any]],
    current_user: User,
    db: AsyncSession,
    on_conflict: str,
    make_global: bool,
) -> ImportResult:
    """Legt Agenten aus geprueften Eintraegen an."""
    res = await db.execute(sa_select(Agent).where(Agent.user_id == current_user.id))
    existing = {a.name: a for a in res.scalars().all()}

    result = ImportResult(created=[], skipped=[], replaced=[], rejected=[])
    for entry in entries:
        name = entry["name"]
        prior = existing.get(name)
        if prior:
            if on_conflict == "skip":
                result.skipped.append(name)
                continue
            if on_conflict == "replace":
                await db.delete(prior)
                await db.flush()
                result.replaced.append(name)
            elif on_conflict == "rename":
                suffix = 2
                while f"{name} ({suffix})" in existing:
                    suffix += 1
                name = f"{name} ({suffix})"
                result.created.append(name)
        else:
            result.created.append(name)

        agent = Agent(
            id=uuid.uuid4(),
            user_id=current_user.id,
            name=name,
            system_prompt=entry["system_prompt"],
            persona_bio=entry.get("persona_bio"),
            llm_provider=entry.get("llm_provider", "openai"),
            llm_base_url=entry.get("llm_base_url"),
            llm_model=entry.get("llm_model"),
            temperature=entry["temperature"],
            web_search_enabled=entry["web_search_enabled"],
            web_search_provider=entry["web_search_provider"],
            searxng_url=entry.get("searxng_url"),
            knowledge_graph_enabled=entry["knowledge_graph_enabled"],
            cache_enabled=entry["cache_enabled"],
            mcp_enabled=entry["mcp_enabled"],
            is_global=make_global,
        )
        db.add(agent)
        existing[name] = agent

    await db.flush()
    return result


@router.post("/agents/import")
async def import_agents(
    payload: Any = Body(..., description="Export-Buendel oder blanke Agentenliste"),
    on_conflict: str = Query("skip", pattern="^(skip|rename|replace)$"),
    make_global: bool = Query(False, description="Importierte Agenten global freigeben (nur Admins)"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Importiert Agenten aus einem JSON-Buendel.

    ``on_conflict`` steuert das Verhalten bei Namensgleichheit: ``skip`` laesst
    Vorhandenes unberuehrt, ``rename`` haengt eine Nummer an, ``replace`` ersetzt.
    """
    if make_global and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Nur Admins duerfen Agenten global freigeben")
    try:
        entries = parse_bundle(payload)
    except ImportValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    result = await _import_agent_entries(entries, current_user, db, on_conflict, make_global)
    logger.info(
        "Agenten-Import fuer %s: %d neu, %d uebersprungen, %d ersetzt",
        current_user.email, len(result.created), len(result.skipped), len(result.replaced),
    )
    return result.as_dict()


@router.post("/agents/import/seed")
async def import_seed_agents(
    on_conflict: str = Query("skip", pattern="^(skip|rename|replace)$"),
    make_global: bool = Query(False, description="Importierte Agenten global freigeben (nur Admins)"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Spielt die mitgelieferte Agenten-Sammlung aus ``seeds/agents.json`` ein.

    Gedacht fuer die Erstinbetriebnahme einer neuen Installation.
    """
    if make_global and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Nur Admins duerfen Agenten global freigeben")

    seed_file = Path(__file__).parent / "seeds" / "agents.json"
    if not seed_file.exists():
        raise HTTPException(status_code=404, detail=f"Seed-Datei nicht gefunden: {seed_file}")
    try:
        entries = parse_bundle(json.loads(seed_file.read_text(encoding="utf-8")))
    except (ImportValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Seed-Datei fehlerhaft: {exc}")

    result = await _import_agent_entries(entries, current_user, db, on_conflict, make_global)
    logger.info("Seed-Import fuer %s: %d neu, %d uebersprungen", current_user.email,
                len(result.created), len(result.skipped))
    return {"source": "seeds/agents.json", **result.as_dict()}


@router.get("/agents/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent_owned_or_global(_uuid_or_400(agent_id), current_user, db)
    return _agent_to_read(agent, current_user.id)


@router.patch("/agents/{agent_id}/global", response_model=AgentRead)
async def set_agent_global(
    agent_id: str,
    req: AgentGlobalPatch,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Gibt einen Agenten global frei bzw. nimmt die Freigabe zurueck — nur fuer Admins.

    Global freigegebene Agenten sind fuer alle registrierten User sichtbar, koennen
    von diesen aber weder bearbeitet noch geloescht werden (nur geklont).
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Nur Admins duerfen Agenten global freigeben")

    sid = _uuid_or_400(agent_id)
    res = await db.execute(
        sa_select(Agent).where(Agent.id == sid, Agent.user_id == current_user.id)
    )
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden oder gehoert dir nicht")

    agent.is_global = req.is_global
    db.add(agent)
    await db.flush()
    logger.info("Agent %s global=%s durch Admin %s", agent.name, req.is_global, current_user.email)
    return _agent_to_read(agent, current_user.id)


@router.post("/agents/{agent_id}/clone", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def clone_agent(
    agent_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Uebernimmt einen globalen Agenten als eigene, bearbeitbare Kopie."""
    source = await _get_agent_owned_or_global(_uuid_or_400(agent_id), current_user, db)

    dup_res = await db.execute(
        sa_select(Agent).where(Agent.user_id == current_user.id, Agent.name == source.name)
    )
    if dup_res.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Du hast bereits einen Agenten namens '{source.name}'")

    clone = _clone_agent_for_user(source, current_user.id)
    db.add(clone)
    await db.flush()
    await db.refresh(clone)
    return _agent_to_read(clone, current_user.id)


@router.put("/agents/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: str,
    req: AgentCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(agent_id)
    res = await db.execute(
        sa_select(Agent).where(
            Agent.id == sid,
            Agent.user_id == current_user.id,
        )
    )
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    if req.name is not None:
        agent.name = req.name.strip()[:128]
    if req.system_prompt is not None:
        agent.system_prompt = str(req.system_prompt)[:2048]
    if req.persona_bio is not None:
        agent.persona_bio = str(req.persona_bio)[:4096]
    if req.llm_provider is not None:
        agent.llm_provider = req.llm_provider
    if req.llm_model is not None:
        agent.llm_model = req.llm_model
    if req.web_search_enabled is not None:
        agent.web_search_enabled = req.web_search_enabled
    if req.web_search_provider is not None:
        agent.web_search_provider = req.web_search_provider
    if req.searxng_url is not None:
        agent.searxng_url = req.searxng_url

    db.add(agent)
    await db.flush()

    return AgentRead(
        id=str(agent.id),
        name=agent.name,
        system_prompt=(agent.system_prompt or ""),
        persona_bio=agent.persona_bio,
        llm_provider=(agent.llm_provider or "openai"),
        llm_model=agent.llm_model,
        web_search_enabled=bool(agent.web_search_enabled),
        web_search_provider=agent.web_search_provider or "duckduckgo",
        searxng_url=agent.searxng_url,
        created_at=str(agent.created_at) if agent.created_at else "",
    )


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(agent_id)
    res = await db.execute(
        sa_select(Agent).where(
            Agent.id == sid,
            Agent.user_id == current_user.id,
        )
    )
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    await db.delete(agent)


# =================================================================== 7. DebateSession CRUD (mit LLM-Bindung + User-Kontext)


class DebateCreate(BaseModel):
    motion: str = Field(min_length=1, max_length=2048)
    project_id: Optional[str] = None
    llm_endpoint_id: Optional[str] = None


class DebateRead(BaseModel):
    id: str
    motion: str
    status: str
    agent_ids: list[str]
    llm_endpoint_id: Optional[str]
    project_name: Optional[str]
    created_at: str

    model_config = {"from_attributes": True}


async def _get_debate_projects(db, user):
    res = await db.execute(sa_select(Project).where(Project.user_id == user.id))
    return {str(p.id): p for p in res.scalars().all()}


@router.post("/debates", response_model=DebateRead)
async def create_debate(
    req: DebateCreate,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    llm_endpoint_id = None
    project_id = None
    proj_name = None

    if req.project_id:
        try:
            project_id = uuid.UUID(req.project_id)
            # Get project name
            prj_res = await db.execute(sa_select(Project).where(Project.id == project_id))
            found_prj = prj_res.scalar_one_or_none()
            if found_prj and found_prj.user_id == current_user.id:
                proj_name = (found_prj.name or "")[:256]
        except ValueError:
            raise HTTPException(status_code=400, detail="Ungültige Projekt-ID")

    if not llm_endpoint_id and not req.project_id:
        svc = LLMEndpointService(db)
        default_ep = await svc.get_default(current_user.id)
        if default_ep:
            llm_endpoint_id = default_ep.id

    session_obj = DebateSession(
        id=uuid.uuid4(),
        user_id=current_user.id,
        project_id=project_id,
        motion=req.motion[:2048],
        status="draft",
        user_llm_endpoint_id=_uuid_or_400(req.llm_endpoint_id) if req.llm_endpoint_id else llm_endpoint_id,
    )
    db.add(session_obj)
    await db.flush()

    return DebateRead(
        id=str(session_obj.id),
        motion=session_obj.motion,
        status=session_obj.status,
        agent_ids=[],
        llm_endpoint_id=str(session_obj.user_llm_endpoint_id) if session_obj.user_llm_endpoint_id else (str(llm_endpoint_id) if llm_endpoint_id else None),
        project_name=proj_name,
        created_at=str(session_obj.created_at) if session_obj.created_at else "",
    )


@router.get("/debates", response_model=list[DebateRead])
async def list_debates(
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        sa_select(DebateSession)
        .where(DebateSession.user_id == current_user.id)
        .order_by(DebateSession.created_at.desc())
    )
    projects_data = await _get_debate_projects(db, current_user)

    return [
        DebateRead(
            id=str(s.id),
            motion=s.motion,
            status=(s.status or "running"),
            agent_ids=[],
            llm_endpoint_id=str(s.user_llm_endpoint_id) if s.user_llm_endpoint_id else None,
            project_name=projects_data.get(str(s.project_id), {}).name if s.project_id and str(s.project_id) in projects_data else None,
            created_at=str(s.created_at) if s.created_at else "",
        )
        for s in res.scalars().all()
    ]


# WebSocket & Background debate registry
active_websockets: dict[str, set[WebSocket]] = {}
active_debate_tasks: dict[str, asyncio.Task] = {}


async def _run_debate_background_task(
    session_id: str,
    orb: DebateOrchestrator,
    max_rounds: int = 15,
    max_duration_minutes: int | None = None,
):
    """Background task executing LLM debate turns and broadcasting results."""
    logger.info("⚡ Background LLM debate loop started for session %s (max_rounds=%s, max_duration_minutes=%s)", session_id, max_rounds, max_duration_minutes)
    try:
        async for turn_output in orb.run_debate(max_rounds=max_rounds, max_duration_minutes=max_duration_minutes):
            logger.info("Debate [%s] turn: %s", session_id, turn_output[:120])
            # Broadcast turn to connected WebSockets
            if session_id in active_websockets:
                ws_list = list(active_websockets[session_id])
                for ws in ws_list:
                    try:
                        await ws.send_json({"type": "turn", "data": turn_output})
                    except Exception as ws_err:
                        logger.warning("WebSocket send error for session %s: %s", session_id, ws_err)
            await asyncio.sleep(0.5)
    except Exception as exc:
        logger.error("Debate background loop exception for session %s: %s", session_id, exc, exc_info=True)
    finally:
        active_debate_tasks.pop(session_id, None)


@router.post("/debates/{debate_id}/start", status_code=status.HTTP_200_OK)
async def start_debate(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,
        )
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    s.status = "running"
    await db.flush()

    # Look up user saved LLM endpoints to inject credentials automatically
    svc = LLMEndpointService(db)
    user_endpoints = await svc.list_endpoints(current_user.id)
    default_ep = next((ep for ep in user_endpoints if ep.is_default), user_endpoints[0] if user_endpoints else None)
    
    ep_map = {}
    for ep in user_endpoints:
        if ep.provider not in ep_map or ep.is_default:
            ep_map[ep.provider] = ep

    max_rounds = 15
    max_duration_minutes = None

    orb = get_orchestrator(str(s.id))
    if not orb:
        agents_cfg = {}
        mod_goal = "Synthesize viewpoints towards a consensus."
        mod_interval = 3
        if s.project_id:
            prj_res = await db.execute(sa_select(Project).where(Project.id == s.project_id))
            prj = prj_res.scalar_one_or_none()
            if prj:
                if prj.user_llm_endpoint_id:
                    p_ep = next((ep for ep in user_endpoints if str(ep.id) == str(prj.user_llm_endpoint_id)), None)
                    if p_ep:
                        default_ep = p_ep
                        ep_map[p_ep.provider] = p_ep
                if prj.moderator_config:
                    mod_goal = prj.moderator_config.get("goal", mod_goal)
                    mod_interval = prj.moderator_config.get("interval_turns", mod_interval)
                    max_rounds = prj.moderator_config.get("max_rounds", 15)
                    max_duration_minutes = prj.moderator_config.get("max_duration_minutes")

                # Auto-Modus: Teilnehmerfeld per LLM zum Thema zusammenstellen
                if (prj.agent_selection_mode or "manual") == "auto":
                    try:
                        picks, rationale, by_id = await _run_auto_selection(prj, current_user, db)
                        await _materialize_auto_agents(picks, by_id, prj, current_user, db)
                        logger.info(
                            "Auto-Auswahl fuer Debatte %s: %s — %s",
                            s.id, ", ".join(p.candidate.name for p in picks), rationale[:200],
                        )
                    except HTTPException:
                        raise
                    except Exception as exc:
                        logger.error("Automatische Agentenauswahl fehlgeschlagen: %s", exc)
                        raise HTTPException(status_code=502, detail=f"Automatische Agentenauswahl fehlgeschlagen: {exc}")

            ag_res = await db.execute(
                sa_select(Agent).where(Agent.project_id == s.project_id, Agent.user_id == current_user.id)
            )
            for a in ag_res.scalars().all():
                agents_cfg[a.name] = await _build_agent_config(a, s.motion, ep_map, default_ep)

        if not agents_cfg:
            all_ag_res = await db.execute(sa_select(Agent).where(Agent.user_id == current_user.id))
            for a in all_ag_res.scalars().all():
                agents_cfg[a.name] = await _build_agent_config(a, s.motion, ep_map, default_ep)

        mod_cfg = ModeratorConfig(goal=mod_goal, interval_turns=mod_interval)
        orb = DebateOrchestrator(
            session_id=str(s.id),
            motion=s.motion,
            agents_config=agents_cfg,
            moderator_cfg=mod_cfg,
            project_id=str(s.project_id) if s.project_id else None,
        )
        await orb.initialize()
        register_orchestrator(str(s.id), orb)
    else:
        if s.project_id:
            prj_res = await db.execute(sa_select(Project).where(Project.id == s.project_id))
            prj = prj_res.scalar_one_or_none()
            if prj and prj.moderator_config:
                max_rounds = prj.moderator_config.get("max_rounds", 15)
                max_duration_minutes = prj.moderator_config.get("max_duration_minutes")

    try:
        await orb.state_mgr.activate_debate()
    except Exception:
        pass

    # Launch background LLM debate loop task if not already running
    if str(s.id) not in active_debate_tasks or active_debate_tasks[str(s.id)].done():
        active_debate_tasks[str(s.id)] = asyncio.create_task(
            _run_debate_background_task(str(s.id), orb, max_rounds=max_rounds, max_duration_minutes=max_duration_minutes)
        )

    return {"status": "started", "session_id": str(s.id)}


@router.post("/debates/{debate_id}/stop", status_code=status.HTTP_200_OK)
async def stop_debate(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,
        )
    )
    s = res.scalar_one_or_none()
    if not s:
        # Verwaister Hintergrund-Task ohne DB-Zeile: er laeuft sonst unstoppbar weiter
        # und verbrennt LLM-Aufrufe. Abbrechen, dann trotzdem 404 melden.
        orphan = active_debate_tasks.pop(debate_id, None)
        if orphan:
            orphan.cancel()
            logger.warning("Verwaisten Debatten-Task %s abgebrochen (keine DB-Zeile)", debate_id)
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    s.status = "stopped"
    await db.flush()

    if str(s.id) in active_debate_tasks:
        active_debate_tasks[str(s.id)].cancel()
        active_debate_tasks.pop(str(s.id), None)

    orb = get_orchestrator(str(s.id))
    if orb:
        try:
            await orb.state_mgr.deactivate_debate()
        except Exception:
            pass

    return {"status": "stopped", "session_id": str(s.id)}


@router.get("/debates/{debate_id}", response_model=DebateRead)
async def get_debate(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,
        )
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    projects_data = await _get_debate_projects(db, current_user)
    return DebateRead(
        id=str(s.id),
        motion=s.motion,
        status=(s.status or "running"),
        agent_ids=[],
        llm_endpoint_id=str(s.user_llm_endpoint_id) if s.user_llm_endpoint_id else None,
        project_name=projects_data.get(str(s.project_id), {}).get("name") if s.project_id and str(s.project_id) in projects_data else None,
        created_at=str(s.created_at) if s.created_at else "",
    )


@router.get("/debates/{debate_id}/turns")
async def get_debate_turns(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,
        )
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    turns = []
    # 1. Read disk log file if available
    log_file = Path(settings.debate_log_dir) / str(s.id) / "turns.jsonl"
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        turns.append(json.loads(line))
        except Exception as exc:
            logger.warning("Error reading debate turn logs for %s: %s", s.id, exc)

    # 2. Fallback to in-memory orchestrator turns
    if not turns:
        orb = get_orchestrator(str(s.id))
        if orb and hasattr(orb, "state") and orb.state.turns:
            for agent_name, text in orb.state.turns:
                turns.append({"agent": agent_name, "content": text})

    return {"session_id": str(s.id), "turns": turns}


async def _load_owned_session(debate_id: str, current_user: User, db: AsyncSession) -> DebateSession:
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(DebateSession.id == sid, DebateSession.user_id == current_user.id)
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")
    return s


def _read_log_entries(session_id: str) -> list[dict[str, Any]]:
    log_file = Path(settings.debate_log_dir) / str(session_id) / "turns.jsonl"
    entries: list[dict[str, Any]] = []
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return entries


@router.get("/debates/{debate_id}/evaluation")
async def get_debate_evaluation(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Liefert die Abschlussauswertung (Zusammenfassung, Fazit, Bewertung) einer Debatte."""
    s = await _load_owned_session(debate_id, current_user, db)
    entries = _read_log_entries(str(s.id))
    syntheses = [e for e in entries if e.get("kind") == "synthesis"]
    if not syntheses:
        raise HTTPException(
            status_code=404,
            detail="Noch keine Auswertung vorhanden — Debatte laeuft noch oder wurde vor der Auswertung abgebrochen. "
                   "Mit POST /debates/{id}/evaluate neu erzeugen.",
        )
    latest = syntheses[-1]
    return {
        "session_id": str(s.id),
        "motion": s.motion,
        "generated_at": latest.get("timestamp"),
        "evaluation": latest.get("content", ""),
    }


@router.post("/debates/{debate_id}/evaluate")
async def evaluate_debate(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Erzeugt die Abschlussauswertung neu — aus den gespeicherten Redebeitraegen.

    Nuetzlich, wenn eine Debatte abgebrochen wurde oder die Auswertung fehlt.
    """
    s = await _load_owned_session(debate_id, current_user, db)
    entries = _read_log_entries(str(s.id))
    turns = [e for e in entries if e.get("kind", "turn") == "turn"]
    if not turns:
        raise HTTPException(status_code=400, detail="Keine Redebeitraege vorhanden — nichts auszuwerten")

    ep_res = await db.execute(
        sa_select(UserLLMEndpoint).where(
            UserLLMEndpoint.user_id == current_user.id,
            UserLLMEndpoint.is_default == True,  # noqa: E712
        )
    )
    ep = ep_res.scalars().first()

    ag_res = await db.execute(
        sa_select(Agent).where(Agent.user_id == current_user.id, Agent.project_id == s.project_id)
    )
    agents = ag_res.scalars().all()
    model = next((a.llm_model for a in agents if a.llm_model), None) or (ep.model if ep else None) or "gpt-4o-mini"
    provider = (agents[0].llm_provider if agents else None) or (ep.provider if ep else "openai")

    orb = DebateOrchestrator(
        session_id=str(s.id),
        motion=s.motion,
        agents_config={"auswerter": {
            "provider": provider,
            "model": model,
            "base_url": (ep.base_url if ep else "") or "",
            "api_key": (ep.api_key_encrypted if ep else "") or "",
        }},
        project_id=str(s.project_id) if s.project_id else None,
    )
    orb.state.turns = [(e.get("agent", "unbekannt"), e.get("content", "")) for e in turns]

    try:
        await orb.memory.initialize()
        evaluation = await orb._run_synthesis()
        orb._write_log_entry("moderator", evaluation, kind="synthesis")
    except Exception as exc:
        logger.error("Nachtraegliche Auswertung fuer %s fehlgeschlagen: %s", s.id, exc)
        raise HTTPException(status_code=502, detail=f"Auswertung fehlgeschlagen: {exc}")
    finally:
        try:
            await orb.memory.close()
            await orb.search.close()
        except Exception:
            pass

    return {"session_id": str(s.id), "turns_evaluated": len(turns), "evaluation": evaluation}


@router.get("/debates/{debate_id}/export")
async def export_debate(
    debate_id: str,
    format: str = Query("markdown", description="Export format: markdown, json, or text"),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,
        )
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    turns = []
    log_file = Path(settings.debate_log_dir) / str(s.id) / "turns.jsonl"
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        turns.append(json.loads(line))
        except Exception as exc:
            logger.warning("Error reading debate turn logs for export %s: %s", s.id, exc)

    if not turns:
        orb = get_orchestrator(str(s.id))
        if orb and hasattr(orb, "state") and orb.state.turns:
            for agent_name, text in orb.state.turns:
                turns.append({"agent": agent_name, "content": text, "timestamp": str(s.created_at)})

    fmt = format.lower().strip()
    clean_motion = (s.motion or "Debatte").replace("\n", " ").strip()
    short_id = str(s.id)[:8]

    if fmt == "json":
        export_data = {
            "session_id": str(s.id),
            "motion": s.motion,
            "status": s.status,
            "created_at": str(s.created_at) if s.created_at else "",
            "turns_count": len(turns),
            "turns": turns,
        }
        json_content = json.dumps(export_data, ensure_ascii=False, indent=2)
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="debatte_{short_id}.json"'},
        )

    elif fmt == "text":
        lines = [
            f"============================================================",
            f"SOVEREIGN DEBATE ENGINE — DEBATTEN-PROTOKOLL",
            f"============================================================",
            f"ID: {s.id}",
            f"THEMA: {clean_motion}",
            f"STATUS: {s.status}",
            f"DATUM: {s.created_at}",
            f"ANZAHL RUNDEN: {len(turns)}",
            f"============================================================\n",
        ]
        for idx, t in enumerate(turns, 1):
            agent = t.get("agent", "REDE").upper()
            ts = t.get("timestamp", "")
            content = t.get("content", "")
            lines.append(f"[{idx}] {agent} ({ts}):\n{content}\n" + "-"*40 + "\n")

        txt_content = "\n".join(lines)
        return Response(
            content=txt_content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="debatte_{short_id}.txt"'},
        )

    else:
        # Default: Markdown (.md)
        md_lines = [
            f"# 🏛️ Debatten-Protokoll: {clean_motion}",
            f"",
            f"- **Debatten-ID**: `{s.id}`",
            f"- **Status**: `{s.status}`",
            f"- **Erstellt am**: `{s.created_at}`",
            f"- **Gesamtbeiträge**: `{len(turns)}`",
            f"",
            f"---",
            f"",
            f"## 💬 Diskurs-Verlauf",
            f"",
        ]

        for idx, t in enumerate(turns, 1):
            agent = t.get("agent", "Teilnehmer").upper()
            content = t.get("content", "")
            
            if "[MODERATOR]" in agent or "[MODERATOR]" in content:
                md_lines.append(f"### ⚖️ Runde {idx}: {agent}\n")
                md_lines.append(f"> {content}\n")
            elif "[SYNTHESIS]" in agent or "[SYNTHESIS]" in content or "[CONSENSUS]" in content:
                md_lines.append(f"### 🏆 Runde {idx}: Synthese / Konsens\n")
                md_lines.append(f"```text\n{content}\n```\n")
            else:
                md_lines.append(f"### 👤 Runde {idx}: {agent}\n")
                md_lines.append(f"{content}\n")

            md_lines.append("---\n")

        md_content = "\n".join(md_lines)
        return Response(
            content=md_content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="debatte_{short_id}.md"'},
        )


@router.delete("/debates/{debate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_debate(
    debate_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sid = _uuid_or_400(debate_id)
    res = await db.execute(
        sa_select(DebateSession).where(
            DebateSession.id == sid,
            DebateSession.user_id == current_user.id,
        )
    )
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    await db.delete(s)
    await db.flush()


# =================================================================== 7. WebSocket Streaming
from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/debates/{debate_id}/stream")
@router.websocket("/projects/{project_id}/debates/{debate_id}/stream")
async def websocket_stream_debate(websocket: WebSocket, debate_id: str, project_id: Optional[str] = None):
    await websocket.accept()
    if debate_id not in active_websockets:
        active_websockets[debate_id] = set()
    active_websockets[debate_id].add(websocket)

    log_file = Path(settings.debate_log_dir) / str(debate_id) / "turns.jsonl"
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        agent_name = entry.get("agent", "AGENT")
                        content = entry.get("content", "")
                        await websocket.send_json({"type": "turn", "data": f"[{agent_name.upper()}]: {content}"})
        except Exception as exc:
            logger.warning("Error streaming disk turn logs via WS for %s: %s", debate_id, exc)
    else:
        orb = get_orchestrator(debate_id)
        if orb and hasattr(orb, 'state') and hasattr(orb.state, 'turns'):
            for agent_name, turn_text in orb.state.turns:
                try:
                    await websocket.send_json({"type": "turn", "data": f"[{agent_name.upper()}]: {turn_text}"})
                except Exception:
                    pass

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from debate session %s", debate_id)
    except Exception as exc:
        logger.error("Error during WebSocket debate stream %s: %s", debate_id, exc)
    finally:
        if debate_id in active_websockets:
            active_websockets[debate_id].discard(websocket)
            if not active_websockets[debate_id]:
                active_websockets.pop(debate_id, None)



# =================================================================== 8. Projekt-Material (Dokumente & Bilder)

class ProjectDocumentRead(BaseModel):
    id: str
    project_id: str
    filename: str
    content_type: str
    kind: str
    size_bytes: int
    description: Optional[str] = None
    extracted_chars: int
    created_at: str


def _doc_to_read(d: ProjectDocument) -> ProjectDocumentRead:
    return ProjectDocumentRead(
        id=str(d.id),
        project_id=str(d.project_id),
        filename=d.filename,
        content_type=d.content_type,
        kind=d.kind,
        size_bytes=int(d.size_bytes or 0),
        description=d.description,
        extracted_chars=int(d.extracted_chars or 0),
        created_at=str(d.created_at) if d.created_at else "",
    )


async def _get_owned_project(project_id: str, current_user: User, db: AsyncSession) -> Project:
    pid = _uuid_or_400(project_id, "project_id")
    res = await db.execute(
        sa_select(Project).where(Project.id == pid, Project.user_id == current_user.id)
    )
    prj = res.scalar_one_or_none()
    if not prj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return prj


@router.post(
    "/projects/{project_id}/documents",
    response_model=ProjectDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_document(
    project_id: str,
    file: UploadFile = File(...),
    description: str = Form(""),
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Laedt Material (Dokument oder Bild) fuer ein Projekt hoch und indexiert es."""
    prj = await _get_owned_project(project_id, current_user, db)

    data = await file.read()
    try:
        ext = document_service.validate_upload(file.filename or "", len(data))
    except UploadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    doc_id = uuid.uuid4()
    try:
        stored_path = await asyncio.to_thread(
            document_service.store_file, str(prj.id), str(doc_id), file.filename or "upload", data
        )
        extraction = await asyncio.to_thread(
            document_service.extract_content, stored_path, description or ""
        )
    except Exception as exc:
        logger.error("Upload-Verarbeitung fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=500, detail="Datei konnte nicht verarbeitet werden")

    chunk_count = await get_document_index().index_document(
        doc_id=str(doc_id),
        project_id=str(prj.id),
        filename=file.filename or stored_path.name,
        kind=extraction.kind,
        text=extraction.text,
    )

    doc = ProjectDocument(
        id=doc_id,
        user_id=current_user.id,
        project_id=prj.id,
        filename=document_service.safe_filename(file.filename or stored_path.name),
        content_type=file.content_type or "application/octet-stream",
        kind=extraction.kind,
        file_path=str(stored_path),
        size_bytes=len(data),
        description=description or None,
        extracted_chars=len(extraction.text),
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    logger.info(
        "Material hochgeladen: %s (%s, %d Bytes, %d Chunks) fuer Projekt %s",
        doc.filename, extraction.kind, len(data), chunk_count, prj.id,
    )
    return _doc_to_read(doc)


@router.get("/projects/{project_id}/documents", response_model=list[ProjectDocumentRead])
async def list_project_documents(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prj = await _get_owned_project(project_id, current_user, db)
    res = await db.execute(
        sa_select(ProjectDocument)
        .where(ProjectDocument.project_id == prj.id)
        .order_by(ProjectDocument.created_at.desc())
    )
    return [_doc_to_read(d) for d in res.scalars().all()]


@router.get("/projects/{project_id}/documents/{doc_id}/download")
async def download_project_document(
    project_id: str,
    doc_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prj = await _get_owned_project(project_id, current_user, db)
    did = _uuid_or_400(doc_id, "doc_id")
    res = await db.execute(
        sa_select(ProjectDocument).where(
            ProjectDocument.id == did, ProjectDocument.project_id == prj.id
        )
    )
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    path = Path(doc.file_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Datei nicht mehr vorhanden")
    return FileResponse(path, filename=doc.filename, media_type=doc.content_type)


@router.delete("/projects/{project_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_document(
    project_id: str,
    doc_id: str,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prj = await _get_owned_project(project_id, current_user, db)
    did = _uuid_or_400(doc_id, "doc_id")
    res = await db.execute(
        sa_select(ProjectDocument).where(
            ProjectDocument.id == did, ProjectDocument.project_id == prj.id
        )
    )
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    await get_document_index().remove_document(str(doc.id))
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Datei %s konnte nicht geloescht werden: %s", doc.file_path, exc)

    await db.delete(doc)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
