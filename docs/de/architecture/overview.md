# Architektur-Überblick

Die Engine ist in vier Schichten aufgeteilt. Die Regel dahinter: **`main.py` enthält
keine Geschäftslogik**, `engine/` kennt keine HTTP-Details, und `services/` kennt
weder HTTP noch den Debattenablauf.

```mermaid
flowchart TB
    subgraph client["Client"]
        UI["Dashboard<br/>(Jinja2 + Vanilla JS)"]
        API_C["REST-Client / curl"]
    end

    subgraph http["HTTP-Schicht"]
        MAIN["main.py<br/>Lifespan, Static, Templates"]
        ROUTER["api_router_v2.py<br/>51 Endpunkte, JWT, Mandantenprüfung"]
        WS["WebSocket<br/>/debates/{id}/stream"]
    end

    subgraph business["Geschäftslogik"]
        ORCH["engine/orchestrator.py<br/>Debattenschleife, Moderation, Auswertung"]
    end

    subgraph svc["Services"]
        LLM["llm_client<br/>OpenAI-kompatibel"]
        SEL["agent_selection<br/>Teilnehmerauswahl"]
        MEM["memory_service<br/>Vektor + Graph"]
        DOC["document_service<br/>Upload, Extraktion"]
        SEARCH["search_service<br/>SearXNG / DuckDuckGo"]
        STATE["state_manager<br/>Kill-Switch, Kosten"]
        USER["user_service<br/>JWT, Passwort-Hash"]
    end

    subgraph data["Persistenz"]
        PG[("PostgreSQL<br/>Nutzer, Projekte, Agenten")]
        NEO[("Neo4j<br/>Diskursgraph")]
        CHR[("ChromaDB<br/>Vektorindex")]
        VAL[("Valkey<br/>Zähler, Kill-Switch")]
        FS[("Dateisystem<br/>Uploads, JSONL-Logs")]
    end

    EXT["Externer LLM-Endpunkt<br/>(OpenAI-kompatibel)"]
    SX["SearXNG"]

    UI --> ROUTER
    API_C --> ROUTER
    UI -.Live-Stream.-> WS
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

## Schichten und ihre Regeln

| Schicht | Verzeichnis | Darf | Darf nicht |
|---------|-------------|------|------------|
| HTTP | `main.py`, `api_router_v2.py` | Requests validieren, Mandanten prüfen, Engine aufrufen | Geschäftslogik enthalten |
| Geschäftslogik | `engine/` | Debattenablauf steuern, Services orchestrieren | HTTP-Objekte kennen |
| Services | `services/` | Externe Systeme kapseln | Debattenablauf kennen |
| Modelle | `models/` | Datenstruktur definieren | Logik enthalten |

## Warum drei Datenspeicher

Die drei Gedächtnisse beantworten unterschiedliche Fragen und sind deshalb nicht
redundant:

- **PostgreSQL** — *Wem gehört was?* Nutzer, Projekte, Agenten, Sessions. Relationale
  Integrität und Mandantentrennung.
- **ChromaDB** — *Was ähnelt dem hier?* Semantische Suche über Redebeiträge und
  hochgeladenes Material. Liefert dem Orchestrator pro Turn passende Quellenausschnitte.
- **Neo4j** — *Wer bezog sich worauf?* Der Diskursgraph aus Agenten, Beiträgen und
  Konzepten. Grundlage der Loop-Erkennung: Verengt sich die Menge der genannten
  Konzepte, dreht sich die Debatte im Kreis.

**Valkey** ist kein Gedächtnis, sondern Steuerung: Kill-Switch, Kosten- und
Rundenzähler. Alle Schlüssel sind pro Session isoliert (`debate:{session_id}:…`),
damit parallele Debatten sich nicht gegenseitig zurücksetzen.

## Datenfluss beim Debattenstart

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as api_router_v2
    participant PG as PostgreSQL
    participant S as agent_selection
    participant O as Orchestrator
    participant T as Hintergrund-Task

    C->>R: POST /debates/{id}/start
    R->>PG: Session + Projekt laden (user_id geprüft)
    alt agent_selection_mode = auto
        R->>S: Motion + Agentenkatalog
        S->>S: LLM wählt Teilnehmer
        S-->>R: Auswahl mit Begründung
        R->>PG: Projekt-Kopien mit vollen Tool-Rechten
    end
    R->>PG: Agenten des Projekts laden
    R->>R: _build_agent_config() je Agent<br/>(Persona, Websuche, Endpunkt)
    R->>O: DebateOrchestrator(agents_config)
    O->>O: initialize() — Neo4j-Schema, Valkey-Keys
    R->>T: asyncio.create_task(run_debate)
    R-->>C: {"status": "started"}
    Note over T: Läuft asynchron weiter,<br/>streamt an WebSocket-Clients
```

!!! warning "Der Hintergrund-Task lebt im App-Prozess"
    `run_debate()` läuft als `asyncio`-Task innerhalb von uvicorn. **Ein Neustart des
    App-Containers bricht laufende Debatten ab.** Vor Deployments prüfen, ob Debatten
    aktiv sind — und Codeänderungen bündeln, statt mehrfach neu zu starten.

## Verzeichnisstruktur

```
├── main.py                      HTTP-Einstieg, Lifespan
├── api_router_v2.py             REST + WebSocket, Mandantentrennung
├── config.py                    Pydantic Settings — einzige Konfigurationsquelle
├── engine/
│   └── orchestrator.py          Debattenschleife, Moderation, Auswertung
├── services/
│   ├── llm_client.py            OpenAI-kompatible Aufrufe, Reasoning-Fallback
│   ├── agent_selection_service.py  KI-gestützte Teilnehmerauswahl
│   ├── persona_library.py       57 vorkonfigurierte Personas
│   ├── memory_service.py        ChromaDB + Neo4j, pro Session isoliert
│   ├── document_service.py      Upload, Textextraktion, Chunking
│   ├── search_service.py        SearXNG mit DuckDuckGo-Fallback
│   ├── state_manager.py         Valkey: Kill-Switch, Kosten, Zähler
│   ├── user_service.py          JWT und Passwort-Hashing (stdlib)
│   └── templates/               Dashboard
├── models/db.py                 SQLAlchemy-Modelle
├── scripts/                     Sync- und Sicherheitsskripte
└── tests/                       114 Tests
```
