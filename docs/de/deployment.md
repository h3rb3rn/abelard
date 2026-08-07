# Deployment-Anleitung

## Voraussetzungen

- Docker + Docker Compose (v2)
- Python 3.12+ (für lokale Entwicklung)
- Mindestens 4 GB RAM für alle Container zusammen

## Docker Compose — Dienstabfrage

| Dienst | Port (Host) | Beschreibung |
|--------|-------------|-------------|
| app | `0.0.0.0:8106` | FastAPI-Anwendung |
| valkey | `0.0.0.0:8101` | State-Management & Kill-Switch |
| neo4j | `0.0.0.0:8102,8103` | Graph-Gedächtnis (Bolt + HTTP) |
| chroma | `0.0.0.0:8104` | Vektor-Datenbank für Retrieval |
| searxng | `0.0.0.0:8105` | Privates Such-Backend |
| postgres | `0.0.0.0:8200` | Projekt/Sitzungs-Persistenz |

## Schnellaufbau

```bash
# Alle Dienste starten (einschließlich PostgreSQL)
docker compose -f abelard/docker-compose.yml up -d --build

# Status prüfen
docker compose -f abelard/docker-compose.yml ps

# Logs beobachten
docker compose -f abelard/docker-compose.yml logs -f app
```

## Environment-Variablen (.env)

Kopiere `.env.example` zu `.env` und passe alle Werte an:

```bash
cp .env.example .env
```

### Pflichtvariablen

| Variable | Beschreibung | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API-Schlüssel (optional) | "" |
| `DEFAULT_PROVIDER` | Standard-LLM: `openai` oder `ollama` | `openai` |
| `OLLAMA_BASE_URL` | Ollama-API-Endpunkt | "" |
| `VALKEY_HOST` | Valkey-Hostname | `valkey` |
| `NEO4J_URI` | Neo4j Bolt-URI | `bolt://neo4j:7687` |
| `POSTGRES_PASSWORD` | PostgreSQL-Passwort (SICHERHEIT!) | — |

## Lokale Entwicklung

```bash
# Abhängigkeiten
cd abelard && poetry install

# Environment-Variablen laden
set -a && source .env && set +a

# App starten
uvicorn main:app --host 0.0.0.0 --port 8106 --reload

# Separate Terminals für Infrastrukturen
docker compose -f abelard/docker-compose.yml up valkey neo4j chroma searxng postgres
```

## Monitoring & Health-Checks

Docker Compose verwendet deterministische Health-Checks:

```bash
# Alle Services prüfen
curl http://localhost:8106/health

# Einzelne Container prüfen
docker inspect --format='{{.State.Health.Status}}' <container-name>
```

## Troubleshooting

### App startet nicht — Zustand "unhealthy"
1. Logs lesen: `docker compose logs app`
2. Prüfe Abhängigkeiten: `docker inspect` aller dependant services
3. Überprüfe `.env` auf korrekte Credentials

### PostgreSQL kann nicht verbinden
- Port 8200 ist korrekt gemappt?
- Container-Name in `.env` = `postgres`?
- Datenbank existiert? → `docker exec <pg-container> psql -U debate -l`

### Valkey/Redis-Timeout
- Prüfe: `docker exec valkey redis-cli ping` (sollte `PONG` zurückgeben)
- Sync-vs-Async Fehler in `state_manager.py` — muss `from redis import asyncio as valkey` verwenden
