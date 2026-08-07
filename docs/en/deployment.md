# Deployment Guide

## Prerequisites

- Docker + Docker Compose (v2)
- Python 3.12+ (for local development)
- Minimum 4 GB RAM combined for all container services

## Docker Compose Services

| Service | Port (Host) | Purpose |
|---------|-------------|---------|
| `app` | `0.0.0.0:8106` | FastAPI Web Application & API |
| `valkey` | `0.0.0.0:8101` | State Management & Session Kill Switch |
| `neo4j` | `0.0.0.0:8102, 8103` | Discourse Graph Storage (HTTP & Bolt) |
| `chroma` | `0.0.0.0:8104` | Vector Database for Context Retrieval |
| `searxng` | `0.0.0.0:8105` | Private Web Search Backend |
| `postgres` | `0.0.0.0:8200` | Relational Persistence for Users, Projects, & Sessions |

## Quick Deployment

```bash
# Start all container services
docker compose up -d --build

# Verify container status
docker compose ps

# Follow application logs
docker compose logs -f app
```

## Environment Configuration (.env)

Copy `.env.example` to `.env` and set all required secrets:

```bash
cp .env.example .env
```

### Required Configuration Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEFAULT_PROVIDER` | Default LLM provider: `openai` or `ollama` | `openai` |
| `OPENAI_API_KEY` | OpenAI API Key (if using OpenAI default) | "" |
| `OLLAMA_BASE_URL` | Ollama API endpoint URL | "" |
| `VALKEY_HOST` | Valkey container hostname | `valkey` |
| `NEO4J_URI` | Neo4j Bolt URI connection string | `bolt://neo4j:7687` |
| `POSTGRES_PASSWORD` | PostgreSQL database password | — |

## Local Development Workflow

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Load environment variables
export $(cat .env | xargs)

# Launch infrastructure containers only
docker compose up -d valkey neo4j chroma searxng postgres

# Launch application with hot-reloading
uvicorn main:app --host 0.0.0.0 --port 8106 --reload
```

## Health Monitoring

Docker Compose uses deterministic health checks across all services:

```bash
# Check application health status
curl http://localhost:8106/health

# Inspect individual container health
docker inspect --format='{{.State.Health.Status}}' abelard-app-1
```

## Troubleshooting

### App Container Status "Unhealthy"
1. Inspect application logs: `docker compose logs app`
2. Check database connection parameters in `.env`
3. Verify all upstream containers report healthy status

### PostgreSQL Connection Failures
- Ensure host port 8200 is bound correctly
- Verify container host name in `.env` matches `postgres`
- Verify database exists: `docker exec <pg-container> psql -U debate -l`

### Valkey Timeouts
- Test connection: `docker exec <valkey-container> valkey-cli ping` (returns `PONG`)
- Verify `state_manager.py` uses `from redis import asyncio as valkey`
