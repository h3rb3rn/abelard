# AGENT.md — Projektvorgaben für Abelard

Diese Datei definiert die verbindlichen Entwicklungsregeln für alle KI-Assistenten (OpenCode, Claude Code und andere Agent-Systeme). Sie gilt für jeden Commit.

---

## 1. Codestyle & Qualität

### Clean Code
- Jede Funktion muss einen prägnanten Namen haben, der das Verhalten beschreibt (`fetch_debate_logs` statt `get_data`)
- maximale Funktionslänge: 40 Zeilen (ohne Blank Lines)
- maximal 3 Verschachtelungsebenen (if/for/with)
- early returns bevorzugen before deep nesting
- keine magic numbers — alle Konstanten als benannte `Final`-Werte

### Lesbarkeit
- Code muss für menschliche Entwickler lesbar sein
- Typ-Hints bei allen Funktionssignaturen und Variablen
- Docstrings nur wenn das WARUM nicht aus dem Code ersichtlich ist
  - FUNKTIONIEREN: Jede öffentliche API-Funktion braucht einen kurzen Docstring (Was, Eingabe, Ausgabe)
  - MODUL: Jede neue Quelldatei braucht eine einzeilige Beschreibung am Anfang als Kommentar

### Verboten
- `any`, `thing`, `data`, `stuff` als Variablennamen
- `TODO`, `FIXME`, `HACK`, `XXX` im Code (wenn etwas nicht fertig ist, lieber wegwerfen)
- Verschlüsselte Passwörter oder API-Keys im Quelltext
- Import von Modulen ohne Versionsbindung

---

## 2. Sprache & Internationalisierung

### Standardsprache
- **Deutsche Kommentare**: Alle Code-Kommentare auf Deutsch
- **Code selbst**: Technischen Begriffe auf Englisch (Funktionen, Variablen, Typen) — keine deutschen Umlaute in Identifikatoren
- **API-Antworten**: Auf Deutsch bei Benutzersichtbaren Fehlern/Statusmeldungen

### Lokalisierung (i18n)
- Alle benutzersichtbaren Texte müssen über Sprachdateien geladen werden
- Speicherort: `abelard/i18n/{locale}/messages.json`
- Unterstützte Sprachen: `de` (Deutsch), `en` (Englisch)
- Neuerstellung einer neuen Sprache bedeutet ein neues Unterverzeichnis mit `messages.json`

**Beispiel Struktur:**
```
i18n/
├── de/
│   └── messages.json    → {"debate_started": "Debatte gestartet", ...}
├── en/
│   └── messages.json    → {"debate_started": "Debate started", ...}
└── locale.py            → Lade- und Caching-Logik für Sprachdateien
```

### Regel
- Niemals Text direkt im Code ausgeben. Immer über `i18n.get("key")` laden
- Unbekannte Keys liefern den Key-Namen zurück (Fallback), nicht None/Leerstring

---

## 3. Dokumentation mit MkDocs

### Aufbau
- Dokumentation wird parallel zur Entwicklung in `/abelard/docs/` erstellt
- mkdocs.yml liegt im Root des Projekts (`/abelard/mkdocs.yml`)
- Dokumentationsquellen: Markdown-Dateien unter `docs/`

### Automatischer Build während der Entwicklung
```bash
cd abelard && pip install mkdocs mkdocs-material
mkdocs serve  # http://localhost:8000
```

### Pflicht — Was dokumentiert werden muss
1. **Jede neue Feature-Pragmatik** → `docs/features/` mit Kurzübersicht
2. **Architekturentscheidungen** → `docs/architecture/decision_logs/` (ADR)
3. **API-Referenz** → `docs/api/` (Endpunkte, Parameter, Antwortschema)
4. **Deployment-Anleitung** → `docs/deployment/`
5. **Sicherheitshinweise** → `docs/security/`
6. **Contributing Guide** → `docs/contributing.md`

### Regel
- Dokumentation muss mit dem Code gleichen Stand haben. Neue Features ODER Bugfixes müssen die Doku aktualisieren. Keine "Dokumentation folgt später"-Kommentare.

---

## 4. Architektur-Vorgaben

### Schichtenmodell
```
main.py          → HTTP-Schicht (FastAPI Router)
engine/          → Business-Logik (Orchestrator, Debate-Lifecycle)
services/        → Infrastruktur (LLM-Clients, DB, Search, State)
models/          → Datenmodelle (Pydantic + SQLAlchemy)
searxng/         → Externe Konfiguration
i18n/            → Sprachdateien
docs/            → MkDocs-Dokumentation
```

### Abhängigkeiten
- Nur Versionsgebundene Dependencies in `pyproject.toml` und `requirements.txt`
- Keine direkten Importe zwischen Schichten (z.B. main.py darf nur engine/, services/ importieren)
- Externe Dienste über Interfaces (Protocol), nicht concrete Implementierungen

---

## 5. Testing

### Tests müssen
- Jede neue Funktion mindestens einen Unit-Test haben
- API-Endpunkte mindestens einen Integrationstest (mit `TestClient`)
- Externe Abhängigkeiten gemockt werden (LLM, Valkey, PostgreSQL)

### Test-Speicherort: `/abelard/tests/`
```
tests/
├── conftest.py              → Shared fixtures
├── test_orchestrator.py     → Engine tests
├── test_llm_client.py       → LLM provider tests
├── test_state_manager.py    → Valkey state tests
├── test_search_service.py   → SearXNG tests
├── test_memory_service.py   → ChromaDB tests
└── conftest/                → Integrationstests
    └── test_api.py          → API-Endpunkt tests
```

---

## 6. Git & Commit-Guidelines

### Commit-Nachrichten
- Imperativ: "Fix state_manager async redis" statt "Fixed ..." oder "fixes ..."
- Prägnant: max. 72 Zeichen, erste Zeile
- Beschreibung nur wenn nötig — maximal 3 Zeilen

### Branching
- Feature-Branches: `feature/` prefix (z.B. `feature/project-crud`)
- Bugfix-Branches: `fix/` prefix (z.B. `fix/redis-timeout`)
- Kein direktes Push auf `main` oder `master`

---

## 7. Sicherheit

### Credentials
- Niemals Passwörter, API-Keys oder Tokens im Quelltext
- Alle Secrets über Umgebungsvariablen (.env)
- `.env` NIEMALS committen (in .gitignore)

### Input Validation
- Alle API-Eingaben über Pydantic-Modelle validieren
- Keine raw user input in Database Queries, Shell Commands oder HTML Templates
- Rate Limiting auf öffentlichen Endpunkten

---

## 8. Docker & Deployment

### Docker Compose Regeln
- Dienste müssen healthcheck haben
- Ports müssen eindeutig sein (keine Kollisionen)
- Sensible Konfiguration über Environment-Variablen, nicht Hardcoded
- Alle Services müssen mit `docker compose up -d --build` startbar sein

---

## Checkliste vor jedem Commit

- [ ] Code folgt Clean-Code-Regeln (max 40 Zeilen/Funktion, early returns)
- [ ] Alle benutzersichtbaren Texte über i18n (nicht hardcoded)
- [ ] Neue Funktionen haben Tests
- [ ] Dokumentation aktualisiert (wenn zutreffend)
- [ ] Keine Secrets im Quelltext
- [ ] Commit-Nachricht im Imperativ, max 72 Zeichen
- [ ] Alle Docker-Services starten fehlerfrei (`docker compose up -d --build`)
