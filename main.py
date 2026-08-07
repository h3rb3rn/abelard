"""FastAPI entrypoint for Abelard.

Clean, modular architecture:
- Database initialization & lifespan management
- Mounts static assets & Jinja2 dashboard
- Includes V2 API Router with multi-tenant isolation & WebSocket streaming
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from models.db import DBManager
from services.org_service import OrgService
from services.deps import set_db_manager, get_db
from api_router_v2 import router as v2router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_db_mgr: DBManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager: initializes DB tables & seeds defaults."""
    global _db_mgr
    # settings liest POSTGRES_URI bereits ueber den Alias db_custom_uri ein —
    # ein zusaetzlicher os.environ-Zugriff waere eine zweite Wahrheitsquelle.
    db_uri = settings.postgres_uri


    logger.info("Initializing database manager...")
    _db_mgr = DBManager(db_uri)
    await _db_mgr.initialize()
    set_db_manager(_db_mgr)
    
    # Ensure default organization exists
    async with _db_mgr.session() as session:
        try:
            org = await OrgService(session).seed_default()
            logger.info("Default organization verified (id=%s)", org.id if org else "n/a")
        except Exception as e:
            logger.warning("Default organization seeding skipped: %s", e)

    yield

    if _db_mgr and hasattr(_db_mgr, 'engine'):
        await _db_mgr.close()
        logger.info("Database connections closed.")


app = FastAPI(
    title="Abelard",
    description="Multi-Agent Philosophical Debate & Moderator Steering Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# Static & Template Mounting
templates = Jinja2Templates(directory="services/templates")
static_dir = Path(__file__).parent / "services" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Mount API V2 Router
app.include_router(v2router, prefix="/api/v2")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Renders the main single-page web UI dashboard."""
    return templates.TemplateResponse("dashboard.html", {"request": {}})


@app.get("/health")
async def health_check():
    """Health check endpoint for Docker & load balancers."""
    return {"status": "ok", "service": "abelard", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    # Bind-Adresse aus der Konfiguration (API_HOST/API_PORT), damit ausserhalb
    # eines Containers nicht ungewollt auf allen Interfaces gelauscht wird.
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)  # nosec B104