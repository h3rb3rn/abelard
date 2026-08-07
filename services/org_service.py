"""Multi-tenant Organization service for abelard."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select as sa_select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from models.db import Organization

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    """Turn a human-readable name into a URL-friendly slug."""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:64]


class OrgService:
    """Lightweight wrapper over Organization queries.

    All methods take a SQLAlchemy async session so callers share transaction scope.
    The class is intentionally stateless; it never holds an open connection outside
    its single operation and never mutates the session except as part of its work.
    """

    __slots__ = ("session",)

    def __init__(self, db: AsyncSession):
        if not isinstance(db, AsyncSession):
            raise TypeError(f"org_session must be an AsyncSession, got {type(db).__name__}")
        self.session = db  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def get_one_by_slug(self, slug: str | None) -> Organization | None:
        if not (slug := (slug or "").strip()):
            return None
        qs = sa_select(Organization).where(Organization.slug == slug[:64])
        res = await self.session.execute(qs)
        org_rows = list(res.scalars().all())
        return org_rows[0] if org_rows else None

    async def get_one_by_id(self, oid: uuid.UUID) -> Organization | None:
        qs = sa_select(Organization).where(Organization.id == oid)
        res = await self.session.execute(qs)
        rows = list(res.scalars().all())
        return rows[0] if rows else None

    async def all(self, limit: int = 50) -> tuple[Organization, ...]:
        effective_limit = max(1, min(limit, 200))
        qs = sa_select(Organization).order_by(Organization.name).limit(effective_limit)
        res = await self.session.execute(qs)
        return tuple(res.scalars().all())

    async def count(self) -> int:
        qs = sa_select(sa_text("count(*)").select_from(Organization))  # type: ignore[arg-type]
        return (await self.session.execute(qs)).scalar() or 0

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create(self, name: str, slug: str | None = None) -> Organization:
        """Create org with idempotency: returns existing if slug already taken."""
        actual_slug = (slug or "").strip()
        if not actual_slug:
            actual_slug = slugify(name)

        org_id = uuid.uuid4()
        org = Organization(
            id=org_id,
            name=name[:256],
            slug=actual_slug[:128],
        )

        try:
            self.session.add(org)
            await self.session.flush()  # triggers unique constraint check
            return org
        except IntegrityError:
            existing = await self.get_one_by_slug(actual_slug)
            if existing:
                logger.info("Organization '%s' already exists, returning existing (slug=%s)", name, actual_slug)
                return existing
            raise

    async def seed_default(self) -> Organization | None:
        """Insert default org 'default' if one does not exist yet."""
        current = await self.get_one_by_slug("default")
        if current is not None:
            return current

        try:
            default_org = await self.create(
                name="Default",
                slug="default",
                
            )
            logger.info("Seeded default organization (id=%s)", default_org.id)
            return default_org
        except IntegrityError:
            # Another process may have seeded it concurrently -- fall back to lookup.
            found = await self.get_one_by_slug("default")
            if found is not None:
                return found
            raise
