# Architektur-Entscheidungsprotokoll (ADR)

## ADR-001: Valkey statt Redis
**Status:** Beschlossen  
**Datum:** 2025-07-08  

### Kontext
Valkey ist die von Linux Foundation betriebene Open-Source-Redis-Fork. Sie sollte als Drop-in Ersatz für Redis verwendet werden.

### Entscheidung
Der Python `redis`-Client wird über den `asyncio`-Submodul (`from redis import asyncio as valkey`) initialisiert. Der Service-Konnektor bleibt `redis://`-URL-basiert (kompatibel mit Valkey).

### Begründung
- `import redis` (sync) funktioniert NICHT mit `await` — führt zu `TypeError: object bool can't be used in 'await' expression`
- `from redis import asyncio as valkey` + `valkey.from_url()` gibt einen korrekten AsyncClient zurück
- Alle anderen Services verbleiben unverändert

### Folgen
- Jede Änderung an state_manager.py muss den async-Pfad verwenden
- sync `redis` kann nicht mehr als Import existieren

---

## ADR-002: SQLAlchemy mit asyncpg statt psycopg
**Status:** Beschlossen  
**Datum:** 2025-07-08  

### Kontext
PostgreSQL für persistente Projekt/Agent/Sitzung-Daten. Wahl des Drivers: `asyncpg` vs `psycopg`.

### Entscheidung
`asyncpg` mit SQLAlchemy 2.x Engine und `AsyncSession`. Session-Factory über `@asynccontextmanager` bereitgestellt.

### Begründung
- asyncpg bietet ~3x bessere Latenz als psycopg im Async-Modus
- asyncio-kompatible Session-Factory vermeidet "missing greenlet" Fehler
- `session()` (sync) zurückgegeben, nicht `async def session()` — verhindert "async generator used as context manager"

---

## ADR-003: Pydantic Settings mit env_file
**Status:** Beschlossen  
**Datum:** 2025-07-08  

### Kontext
Konfiguration muss aus `.env` und Umgebungsvariablen geladen werden.

### Entscheidung
`pydantic_settings.BaseSettings` mit `env_file=".env"` als einziger Konfigurationsquelle.

### Begründung
- Environment-Überschreibung (`os.environ`) hat Vorrang vor `.env` (Pydantic-Verhalten)
- `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")` definiert Source
- Defaults nur für lokale Entwicklung, nicht für Secrets

### Folgen
- Alle Secrets müssen über .env — hardcoded Defaults sind verboten
- `.env.example` ohne echte Keys bereitstellen

---

## ADR-004: i18n-Pattern für benutzersichtbare Texte
**Status:** Beschlossen  
**Datum:** 2025-07-08  

### Kontext
Alle UI-Antworten müssen mehrsprachig sein (Deutsch + Englisch).

### Entscheidung
JSON-Sprachdateien unter `i18n/{locale}/messages.json` mit Caching-Layer. Alle API-Antworten verwenden `i18n.get(key, locale)`.

### Begründung
- JSON ist einfach zu pflegen und zu versionieren
- Neuerungen: Fallback auf Key-Namen wenn Schlüssel fehlt (kein None/Empty)
- `de` als Default-Sprache für die Debatte

---

## ADR-005: Dokumentationsstrategie mit MkDocs Material
**Status:** Beschlossen  
**Datum:** 2025-07-08  

### Kontext
Dokumentation muss parallel zur Entwicklung gepflegt werden.

### Entscheidung
MkDocs mit mkdocs-material Theme. `mkdocs.yml` im Projektroot. Markdown-Dateien unter `docs/`.

### Begründung
- Material Theme unterstützt Dark/Light Mode (wichtig für Lesbarkeit)
- Suchfunktion eingebaut — wichtig für API-Referenz
- Navigation als Tabs/Sektionen — schnelle Orientierung
- Code-Highlighting und SuperFences für Snippets
- Dokumentation muss mit jedem Feature-Bugfix parallel aktualisiert werden
