# Abelard

> Multi-agent debate platform featuring GraphRAG memory, AI-powered moderation,
> and reasoned final evaluations — fully self-hostable and sovereign.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![German Version](https://img.shields.io/badge/Language-German%20%2F%20Deutsch-de.svg)](README-DE.md)

---

## Overview

Multiple LLM agents with elaborate personas discuss a topic or motion over several rounds. What sets Abelard apart is not just *that* they debate — but how the system prevents debates from becoming superficial or repetitive:

- A **Moderator** intervenes at fixed intervals, feeding corrections back directly into the agents' context rather than just the output stream.
- **Loop Detection** evaluates the discourse graph to detect narrow or repeating topics and sets a new focus when needed.
- The **Final Evaluation** goes beyond summarizing: it assesses the debate on exhaustion degree, result plausibility, and source quality (each rated 1–10 with detailed reasoning).

```mermaid
flowchart LR
    M["Motion"] --> S{"Participant<br/>selection"}
    S -->|Manual| A["Manually assigned<br/>agents"]
    S -->|Automatic| B["AI selects matching<br/>agents for topic"]
    A --> D["Debate rounds"]
    B --> D
    MAT["Project Materials<br/>PDF, DOCX, Images"] -.Citable.-> D
    WEB["Web Search<br/>SearXNG"] -.Sources.-> D
    D --> MOD["Moderation"]
    MOD -->|"Corrections<br/>fed back"| D
    D --> E["Evaluation<br/>Conclusion + 3 ratings"]
```

## Key Features

| Feature | Description |
|---|---|
| **57 Personas** | 50 real historical & contemporary scientists across physics, quantum physics, chemistry, mathematics, computer science, AI, astrophysics, astronomy, and quantum computing — plus seven famous fictional AIs. Each persona comes with a biography, key publications/works, and a distinct argumentation style. |
| **Automated Selection** | The AI curates the most suitable panel of participants based on the motion, deliberately fostering opposing viewpoints — a panel of identical minds yields no real debate. |
| **Project Materials** | Upload documents and images; relevant excerpts are retrieved per turn and cited directly by the agents. |
| **Real Research** | Web search via SearXNG with DuckDuckGo fallback instead of hallucinated references. |
| **Dual Memory System** | ChromaDB for semantic vector similarity, Neo4j for the discourse graph structure. |
| **Multi-Tenancy** | Strict user data isolation. Admins can share agents globally; users can clone them into their own scope. |
| **Guardrails** | Session and global cost, round, and time limits accompanied by a real-time kill switch. |

## Architecture

```mermaid
flowchart TB
    subgraph http["HTTP Layer"]
        MAIN["main.py"]
        ROUTER["api_router_v2.py<br/>51 endpoints"]
    end
    subgraph business["Business Logic"]
        ORCH["engine/orchestrator.py"]
    end
    subgraph svc["Services"]
        LLM["llm_client"]
        SEL["agent_selection"]
        MEM["memory_service"]
        DOC["document_service"]
        SEARCH["search_service"]
        STATE["state_manager"]
    end
    subgraph data["Persistence Layer"]
        PG[("PostgreSQL")]
        NEO[("Neo4j")]
        CHR[("ChromaDB")]
        VAL[("Valkey")]
    end
    EXT["External LLM Endpoint"]

    MAIN --> ROUTER
    ROUTER --> ORCH
    ROUTER --> SEL
    ROUTER --> PG
    ORCH --> LLM
    ORCH --> MEM
    ORCH --> SEARCH
    ORCH --> STATE
    ORCH --> DOC
    SEL --> LLM
    LLM --> EXT
    MEM --> NEO
    MEM --> CHR
    DOC --> CHR
    STATE --> VAL
```

Strict Layering Rules: `main.py` contains no business logic, `engine/` is unaware of HTTP details, and `services/` contains neither HTTP logic nor debate lifecycle flows.

### Why Three Data Stores?

| Store | Key Question Answered | Primary Use Case |
|-------|----------------------|------------------|
| PostgreSQL | Who owns what? | Multi-tenancy, projects, user agents |
| ChromaDB | What is semantically similar? | Relevant material excerpts per turn |
| Neo4j | Who referenced what? | Loop detection via concept density |

Valkey is not memory, but operational control: kill switch, cost tracking, and round counters, isolated per session.

## Debate Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant V as Valkey
    participant D as Material Index
    participant L as LLM
    participant M as Memory Stores

    loop Per Turn / Contribution
        O->>V: Check limits (time, rounds, cost, kill switch)
        O->>O: Build context<br/>Key points + last 6 turns full text
        O->>D: Fetch matching material excerpts
        O->>L: Generate contribution
        O->>O: Repetitive contribution? → Retry
        O->>M: Store in ChromaDB + Neo4j + JSONL
        alt Every N contributions
            O->>L: Moderator evaluation
            O->>O: Inject correction as turn
        end
    end
    O->>L: Final evaluation (8192 max tokens)
    O->>M: Persist as kind=synthesis
```

> **Important:** The debate loop runs as an `asyncio` task within the main application process. Restarting the container will terminate ongoing active debates.

## Quickstart

```bash
git clone <repo-url> && cd abelard

cp .env.example .env
# Set required secret values (generate each with `openssl rand -hex 32`):
#   POSTGRES_PASSWORD, NEO4J_PASSWORD, JWT_SECRET

docker compose up -d --build
curl http://localhost:8106/health
```

- **Dashboard**: `http://localhost:8106/`
- **OpenAPI / Swagger Docs**: `http://localhost:8106/docs`

**First Steps in the Web Interface:**
1. Register a new user account.
2. Navigate to **LLM Endpoints**, add your API key/credentials, and set it as default.
3. Click **"🎓 Persona Library"** to seed all 57 personas.
4. Create a new project with a debate motion and launch the debate.

## Configuration

Configuration is managed via environment variables (`config.py`, Pydantic Settings).
**No passwords or API keys are hardcoded in the codebase.** Missing required secrets trigger warnings in development and cause immediate startup termination when `ENVIRONMENT=production`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | Setting to `production` enforces strong secrets |
| `DEFAULT_PROVIDER` | `openai` | Supports any OpenAI-compatible API endpoint |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | – / `gpt-4o-mini` | Fallback model for agents without custom choice |
| `POSTGRES_PASSWORD` | – | **Required** |
| `NEO4J_PASSWORD` | – | **Required** |
| `JWT_SECRET` | – | **Required** (random per start if omitted) |
| `SEARXNG_BASE_URL` | `http://searxng:8080` | Web search backend for agents |
| `UPLOAD_DIR` / `DEBATE_LOG_DIR` | `/data/uploads` / `/data/debate-logs` | Storage paths for files and logs |
| `COST_THRESHOLD_USD` | `5.0` | Cost guardrail per debate session |
| `MODERATOR_INTERVAL` | `3` | Number of turns between moderator interventions |

Full documentation available at [`docs/konfiguration.md`](docs/konfiguration.md).

Deployment-specific overrides (such as `extra_hosts`) belong in `docker-compose.override.yml`. A template is provided in `docker-compose.override.yml.example`; the active file is excluded from version control.

## Dependencies

- **Python 3.12+**
- Runtime requirements: `requirements.txt`
- Development tools: `requirements-dev.txt`

**Runtime Stack:** FastAPI, uvicorn, Jinja2, python-multipart, SQLAlchemy (async), asyncpg, neo4j, chromadb, redis[hiredis], httpx, pydantic, pydantic-settings, pypdf, python-docx, pillow.

**Deliberate Omissions:**
- **No `openai` SDK:** All API calls are executed directly via `httpx` against OpenAI-compatible HTTP endpoints. This ensures support for custom LLM gateways without SDK dependency constraints.
- **No `python-jose` / `passlib`:** JWT signing and password hashing utilize standard library primitives (`hmac`, `hashlib`).

**External Services** (defined in `docker-compose.yml`): PostgreSQL 17, Neo4j 5, ChromaDB, Valkey, SearXNG. The LLM endpoint is *not* packaged in the stack; users specify their own in their account settings.

See [`docs/abhaengigkeiten.md`](docs/abhaengigkeiten.md) for further details.

## Development

```bash
pip install -r requirements-dev.txt

pytest tests/ -v                                  # Runs unit & integration test suite
pytest tests/ --cov=. --cov-report=term-missing
ruff check .
mkdocs serve                                      # Runs documentation server on :8000
```

Before committing/publishing changes:

```bash
bash scripts/run_security_scan.sh       # Secret scanning, config audit, Bandit, Trivy
bash scripts/sync-to-publish.sh --dry-run
```

> Note: `tests/critical-fixes/` runs in an isolated container environment and is excluded locally via `pyproject.toml`.

## Documentation

```bash
pip install mkdocs mkdocs-material && mkdocs serve
```

| Topic | File |
|-------|------|
| Architecture & Layering | [`docs/en/architecture/overview.md`](docs/en/architecture/overview.md) |
| Debate Lifecycle | [`docs/en/architecture/debate-lifecycle.md`](docs/en/architecture/debate-lifecycle.md) |
| Data Models (ER, Graph, Vectors) | [`docs/en/architecture/data-models.md`](docs/en/architecture/data-models.md) |
| Configuration | [`docs/en/configuration.md`](docs/en/configuration.md) |
| Dependencies | [`docs/en/dependencies.md`](docs/en/dependencies.md) |
| API Reference | [`docs/en/api-reference.md`](docs/en/api-reference.md) |
| Personas & Evaluation | [`docs/en/personas-and-evaluation.md`](docs/en/personas-and-evaluation.md) |
| Automatic Agent Selection | [`docs/en/automatic-agent-selection.md`](docs/en/automatic-agent-selection.md) |
| Global Agents | [`docs/en/global-agents.md`](docs/en/global-agents.md) |
| Project Materials / Uploads | [`docs/en/uploads.md`](docs/en/uploads.md) |
| Publishing & Release | [`docs/en/publishing.md`](docs/en/publishing.md) |

## Security Considerations

- No credential defaults in source code; `ENVIRONMENT=production` enforces non-trivial secrets.
- Strict multi-tenancy enforcement on every database query; requests for non-owned objects return `404 Not Found` rather than `403 Forbidden` to prevent object enumeration.
- JWT signing and password hashing are implemented with standard library primitives to minimize third-party supply chain risks.

## License

[MIT](LICENSE)
