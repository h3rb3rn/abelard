# Architecture Overview

The engine is divided into four distinct layers. The core architectural rule: **`main.py` contains no business logic**, `engine/` is unaware of HTTP details, and `services/` contains neither HTTP logic nor debate lifecycle workflows.

```mermaid
flowchart TB
    subgraph client["Client Layer"]
        UI["Dashboard<br/>(Jinja2 + Vanilla JS)"]
        API_C["REST Client / curl"]
    end

    subgraph http["HTTP Layer"]
        MAIN["main.py<br/>Lifespan, Static, Templates"]
        ROUTER["api_router_v2.py<br/>51 endpoints, JWT, Multi-tenancy"]
        WS["WebSocket<br/>/debates/{id}/stream"]
    end

    subgraph business["Business Logic"]
        ORCH["engine/orchestrator.py<br/>Debate loop, Moderation, Evaluation"]
    end

    subgraph svc["Services Layer"]
        LLM["llm_client<br/>OpenAI-compatible"]
        SEL["agent_selection<br/>Participant selection"]
        MEM["memory_service<br/>Vector + Graph"]
        DOC["document_service<br/>Upload, Extraction"]
        SEARCH["search_service<br/>SearXNG / DuckDuckGo"]
        STATE["state_manager<br/>Kill switch, Costs"]
        USER["user_service<br/>JWT, Password hashing"]
    end

    subgraph data["Persistence Layer"]
        PG[("PostgreSQL<br/>Users, Projects, Agents")]
        NEO[("Neo4j<br/>Discourse Graph")]
        CHR[("ChromaDB<br/>Vector Index")]
        VAL[("Valkey<br/>Counters, Kill switch")]
        FS[("File System<br/>Uploads, JSONL logs")]
    end

    EXT["External LLM Endpoint<br/>(OpenAI-compatible)"]
    SX["SearXNG"]

    UI --> ROUTER
    API_C --> ROUTER
    UI -.Live Stream.-> WS
    MAIN --> ROUTER
    ROUTER --> ORCH
    ROUTER --> SEL
    ROUTER --> DOC
    ROUTER --> USER
    ORCH --> LLM
    ORCH --> MEM
    ORCH --> SEARCH
    ORCH --> STATE
    ORCH --> DOC
    SEL --> LLM
    LLM --> EXT
    SEARCH --> SX
    USER --> PG
    ROUTER --> PG
    MEM --> NEO
    MEM --> CHR
    DOC --> CHR
    DOC --> FS
    STATE --> VAL
    ORCH --> FS
```

## Layer Responsibilities and Constraints

| Layer | Directory | Allowed To | Forbidden From |
|-------|-----------|------------|----------------|
| HTTP | `main.py`, `api_router_v2.py` | Validate requests, check tenancy, call engine | Containing business logic |
| Business Logic | `engine/` | Control debate flow, orchestrate services | Handling HTTP objects |
| Services | `services/` | Encapsulate external systems | Handling debate lifecycle |
| Models | `models/` | Define data schemas | Containing business logic |

## Why Three Data Stores?

The three memory stores answer distinct structural questions and are non-redundant:

- **PostgreSQL** — *Who owns what?* Users, projects, agents, sessions. Relational integrity and strict multi-tenancy enforcement.
- **ChromaDB** — *What is semantically similar?* Vector search across debate contributions and uploaded materials. Serves matching material excerpts to the orchestrator per turn.
- **Neo4j** — *Who referenced what?* The discourse graph connecting agents, contributions, and concepts. Serves as the foundation for loop detection: if concept density narrows, the debate is repeating itself.

**Valkey** is operational state management, not memory: kill switch, cost counters, and turn tracking. All keys are isolated per session (`debate:{session_id}:...`) to ensure parallel debates do not interfere with each other.

## Data Flow at Debate Launch

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as api_router_v2
    participant PG as PostgreSQL
    participant S as agent_selection
    participant O as Orchestrator
    participant T as Background Task

    C->>R: POST /debates/{id}/start
    R->>PG: Load Session + Project (verify user_id)
    alt agent_selection_mode = auto
        R->>S: Motion + Agent Catalog
        S->>S: LLM selects participants
        S-->>R: Selection with reasoning
        R->>PG: Copy project agents with tool permissions
    end
    R->>PG: Load project agents
    R->>R: _build_agent_config() per agent<br/>(Persona, Web search, Endpoint)
    R->>O: DebateOrchestrator(agents_config)
    O->>O: initialize() — Neo4j schema, Valkey keys
    R->>T: asyncio.create_task(run_debate)
    R-->>C: {"status": "started"}
    Note over T: Continues asynchronously,<br/>streams to WebSocket clients
```

!!! warning "The background task lives inside the app process"
    `run_debate()` runs as an `asyncio` task inside uvicorn. **Restarting the app container terminates active debates.** Verify whether debates are active before deployments and bundle changes rather than restarting frequently.

## Directory Structure

```
├── main.py                      HTTP entrypoint, Lifespan
├── api_router_v2.py             REST + WebSocket, Multi-tenancy
├── config.py                    Pydantic Settings — single source of configuration
├── engine/
│   └── orchestrator.py          Debate loop, moderation, evaluation
├── services/
│   ├── llm_client.py            OpenAI-compatible calls, reasoning fallback
│   ├── agent_selection_service.py  AI-assisted participant selection
│   ├── persona_library.py       57 pre-configured personas
│   ├── memory_service.py        ChromaDB + Neo4j, isolated per session
│   ├── document_service.py      Upload, text extraction, chunking
│   ├── search_service.py        SearXNG with DuckDuckGo fallback
│   ├── state_manager.py         Valkey: Kill switch, cost, counters
│   ├── user_service.py          JWT and password hashing (stdlib)
│   └── templates/               Dashboard templates
├── models/db.py                 SQLAlchemy ORM models
├── scripts/                     Sync and security scripts
└── tests/                       114 unit & integration tests
```
