"""SQLAlchemy models for user-based multi-tenant isolation.

Tenant-Schema:
    User (Tenant) -> owns Agent, Project, DebateSession, Knowledge
    
Mandatory: Alle FK Tabellen haben user_id als Mandatory (kein NULL)!
Organization ist nur optional f.r backward compat.

Alle Queries MUssen nach user_id filtern!
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, Any

from sqlalchemy import Boolean, ForeignKey, Float, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.dialects.postgresql import UUID as Uuid, TIMESTAMP


# ===================================================================
# Base
# ===================================================================

class Base(DeclarativeBase):
    pass


# ===================================================================
# 1. User (Tenant-Basis)
# ===================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(128), default="")
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    settings_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # darf Agenten global schalten
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    llm_endpoints: Mapped[list["UserLLMEndpoint"]] = relationship("UserLLMEndpoint", back_populates="user", cascade="all, delete-orphan")
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="user", cascade="all, delete-orphan")
    projects: Mapped[list["Project"]] = relationship("Project", back_populates="user", cascade="all, delete-orphan")
    debate_sessions: Mapped[list["DebateSession"]] = relationship("DebateSession", back_populates="user", cascade="all, delete-orphan")
    kv_entries: Mapped[list["KVStore"]] = relationship("KVStore", back_populates="user", cascade="all, delete-orphan")


# ===================================================================
# 2. Organization (optional, backward compat)
# ===================================================================

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256))
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


# ===================================================================
# 3. Agent (mandatory user_id FK)
# ===================================================================

class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    persona_bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_provider: Mapped[str] = mapped_column(String(32), default="openai")
    llm_base_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    skills_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    knowledge_graph_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    web_search_provider: Mapped[Optional[str]] = mapped_column(String(32), default="duckduckgo")
    searxng_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Global freigegebene Agenten sind fuer alle registrierten User sichtbar (read-only)
    # und koennen von ihnen in den eigenen Mandanten geklont werden.
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="agents")
    project = relationship("Project", back_populates="agents")


# ===================================================================
# 4. Project (mandatory user_id FK, organization optional)
# ===================================================================

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(256))
    motion: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    moderator_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # "manual" = fest zugewiesene Agenten, "auto" = KI-gestuetzte Auswahl zum Thema
    agent_selection_mode: Mapped[str] = mapped_column(String(16), default="manual")
    auto_agent_count: Mapped[int] = mapped_column(Integer, default=4)
    user_llm_endpoint_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("user_llm_endpoints.id", ondelete="SET NULL"), nullable=True)  # User gewählter LLM-Endpoint pro Projekt
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="projects")
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="project", cascade="all, delete-orphan")
    debate_sessions: Mapped[list["DebateSession"]] = relationship("DebateSession", back_populates="project", cascade="all, delete-orphan")
    documents: Mapped[list["ProjectDocument"]] = relationship("ProjectDocument", back_populates="project", cascade="all, delete-orphan")
    user_llm_config = relationship("UserLLMEndpoint", back_populates="projects")


# ===================================================================
# 5. DebateSession (mandatory user_id FK + project_id FK)
# ===================================================================

class DebateSession(Base):
    __tablename__ = "debate_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    motion: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    user_llm_endpoint_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("user_llm_endpoints.id", ondelete="SET NULL"), nullable=True)
    json_log_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="debate_sessions")
    project = relationship("Project", back_populates="debate_sessions")


# ===================================================================
# 5b. ProjectDocument (Debatten-Material: Dokumente & Bilder)
# ===================================================================

class ProjectDocument(Base):
    """Hochgeladenes Material (Dokumente/Bilder) eines Projekts."""
    __tablename__ = "project_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    kind: Mapped[str] = mapped_column(String(16), default="document")  # document | image
    file_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_chars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="documents")


# ===================================================================
# 6. KVStore (mandatory user_id FK)
# ===================================================================

class KVStore(Base):
    """Benutzerkontext und Wissen - JSON key-value pro Tenant"""
    __tablename__ = "kv_store"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(256))
    value_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    meta_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="kv_entries")


# ===================================================================
 # 7. UserLLMEndpoint (mandatory user_id FK — self-managed LLM config)
# ===================================================================

class UserLLMEndpoint(Base):
    """Pro User gespeicherte OpenAI/Ollama API Endpoints."""
    __tablename__ = "user_llm_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))  # openai | ollama | custom
    base_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="llm_endpoints")
    projects = relationship("Project", back_populates="user_llm_config", cascade="all, delete-orphan")


# ===================================================================
 # DBManager (async)
# ===================================================================

class DBManager:
    def __init__(self, db_url: str):
        self.engine: AsyncEngine = create_async_engine(db_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def initialize(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            try:
                await conn.execute(sa_text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS persona_bio TEXT;"))
                await conn.execute(sa_text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS web_search_enabled BOOLEAN DEFAULT FALSE;"))
                await conn.execute(sa_text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS web_search_provider VARCHAR(32) DEFAULT 'duckduckgo';"))
                await conn.execute(sa_text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS searxng_url VARCHAR(512);"))
                await conn.execute(sa_text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_global BOOLEAN DEFAULT FALSE;"))
                await conn.execute(sa_text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;"))
                await conn.execute(sa_text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS agent_selection_mode VARCHAR(16) DEFAULT 'manual';"))
                await conn.execute(sa_text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS auto_agent_count INTEGER DEFAULT 4;"))
            except Exception:
                pass

    def session(self) -> AsyncSession:
        return self.session_factory()

    async def close(self):
        await self.engine.dispose()
