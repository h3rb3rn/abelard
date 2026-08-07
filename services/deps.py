"""Database dependency for FastAPI — shared between main and v2 router."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

_db_manager = None
_orchestrators: dict = {}


def set_db_manager(db_mgr):
    """Inject the global DBManager instance (called by app lifespan)."""
    global _db_manager
    _db_manager = db_mgr


def get_orchestrator(session_id: str):
    """Get active orchestrator instance by session ID."""
    return _orchestrators.get(session_id)


def register_orchestrator(session_id: str, orb):
    """Register an active orchestrator instance."""
    _orchestrators[session_id] = orb


def remove_orchestrator(session_id: str):
    """Unregister an orchestrator instance."""
    _orchestrators.pop(session_id, None)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if not _db_manager:
        raise RuntimeError("Database not initialized")

    async with _db_manager.session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

