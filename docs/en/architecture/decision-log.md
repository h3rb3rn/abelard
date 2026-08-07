# Architecture Decision Log (ADR)

## ADR-001: Valkey over Redis
**Status:** Accepted  
**Date:** 2025-07-08  

### Context
Valkey is the open-source Redis fork backed by the Linux Foundation. It serves as a drop-in replacement for Redis.

### Decision
The Python `redis` client is initialized via the `asyncio` submodule (`from redis import asyncio as valkey`). Service connection URLs remain `redis://` based.

### Rationale
- `import redis` (synchronous) does NOT work with `await` — resulting in `TypeError: object bool can't be used in 'await' expression`.
- `from redis import asyncio as valkey` combined with `valkey.from_url()` returns a proper `AsyncClient`.
- All other service layers remain unmodified.

### Consequences
- Any modification to `state_manager.py` must use the asynchronous code path.
- Synchronous `redis` imports are strictly forbidden.

---

## ADR-002: SQLAlchemy with asyncpg over psycopg
**Status:** Accepted  
**Date:** 2025-07-08  

### Context
PostgreSQL stores persistent project, agent, and session records. Driver choice: `asyncpg` vs `psycopg`.

### Decision
Use `asyncpg` with SQLAlchemy 2.x Engine and `AsyncSession`. The session factory is exposed via an `@asynccontextmanager`.

### Rationale
- `asyncpg` provides ~3x lower latency compared to `psycopg` in asynchronous mode.
- An `asyncio`-compatible session factory prevents "missing greenlet" execution errors.

---

## ADR-003: Pydantic Settings with env_file
**Status:** Accepted  
**Date:** 2025-07-08  

### Context
Application configuration must be loaded from `.env` files and system environment variables.

### Decision
Use `pydantic_settings.BaseSettings` with `env_file=".env"` as the single source of truth for configuration.

### Rationale
- Environment overrides (`os.environ`) strictly take precedence over `.env` key-values.
- Hardcoded defaults are restricted to local development settings; secrets must never have default values.

### Consequences
- All sensitive credentials must be supplied via `.env` or system variables.
- An anonymized `.env.example` file must be maintained without actual secret keys.

---

## ADR-004: i18n Pattern for User-Facing Strings
**Status:** Accepted  
**Date:** 2025-07-08  

### Context
All UI and API responses must support internationalization (German + English).

### Decision
Maintain JSON locale files under `locales/{lang}.json` with a memory caching layer. All API responses query `i18n.get(key, locale)`.

### Rationale
- JSON files are straightforward to maintain, audit, and version control.
- Missing translation keys fall back to key names rather than empty strings or nulls.

---

## ADR-005: Documentation Strategy with MkDocs Material
**Status:** Accepted  
**Date:** 2025-07-08  

### Context
Documentation must be maintained in sync with codebase evolution.

### Decision
Use MkDocs with the `mkdocs-material` theme. `mkdocs.yml` resides in the project root with markdown files in `docs/`.

### Rationale
- The Material theme natively supports light/dark mode.
- Built-in instant search facilitates quick lookup of API endpoints and configuration flags.
