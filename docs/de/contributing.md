# Contributing Guide

## Entwicklungs-Workflow

### 1. Branch erstellen
```bash
git checkout -b feature/bezeichnung  # fuer neue Features
git checkout -x fix/bezeichnung      # fuer bugfixes
```

### 2. Code schreiben
- Clean-Code-Regeln befolgen (max 40 Zeilen/Funktion)
- Typ-Hints ueberall (`def func(x: int) -> str:`)
- Deutsche Kommentare, englische Identifikatoren
- Keine magic numbers — benannte Konstanten verwenden

### 3. Tests schreiben
```bash
# Unit tests
pytest tests/test_orchestrator.py -v

# API integration tests
pytest tests/conftest/ -v --async-mode auto
```

### 4. Dokumentation aktualisieren
- Neue Features → `docs/features/` + mkdocs.yml Navigation anpassen
- Bugfixes → Changelog/Emitte in der relevanten Feature-Seite

### 5. Commit-Nachricht
```
Feature: debatten-beschreibung-einfuegen

Beschreibung falls noetig (max 3 zeilen).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

## Docker Development

```bash
# Vollstaendiges setup
cd abelard && docker compose -f docker-compose.yml up -d --build

# logs verfolgen
docker compose -f abelard/docker-compose.yml logs -f app
```

## Code-Review-Kriterien (automatisch von AGENT.md)

- [ ] Clean Code? (max 40 Zeilen/Funktion, early returns)
- [ ] Typ-Hints ueberall?
- [ ] Tests hinzugefuegt?
- [ ] Dokumentation aktualisiert?
- [ ] Keine Secrets im Quelltext?
- [ ] i18n fuer benutzersichtbare Texte?
- [ ] Docker-Stack startet fehlerfrei?
