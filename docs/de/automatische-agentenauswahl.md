# Automatische Agentenauswahl

Ein Projekt kann seine Teilnehmer entweder fest zugewiesen bekommen (`manual`)
oder sie **KI-gestützt zum Thema** auswählen lassen (`auto`).

## Wie die Auswahl funktioniert

1. **Kandidatenpool:** alle eigenen Agenten plus alle global freigegebenen
   (siehe [Globale Agenten](globale-agenten.md)). Bereits automatisch angelegte
   Projekt-Kopien werden ausgeschlossen, damit sich die Auswahl nicht selbst füttert.
2. **Analyse:** Motion und ein kompakter Agentenkatalog gehen an den **im Profil
   favorisierten LLM-Endpoint** (`is_default` in `user_llm_endpoints`).
   Die Auswahl läuft mit `temperature=0.2`, weil sie reproduzierbar sein soll.
3. **Kriterien** (in dieser Reihenfolge): fachliche Nähe zum Thema, *Gegensätzlichkeit*
   der Positionen, methodische Breite. Ein Feld aus lauter Gleichgesinnten erzeugt keine Debatte.
4. **Materialisierung:** Für jeden Treffer entsteht eine **Projekt-Kopie** mit vollen
   Werkzeugrechten. Originale bleiben unangetastet — auch fremde globale Agenten.

### Volle Werkzeugrechte

Automatisch ausgewählte Agenten erhalten immer:

| Recht | Wert |
|-------|------|
| `web_search_enabled` | `true` |
| `web_search_provider` | SearXNG (bzw. der am Original gesetzte Provider) |
| `searxng_url` | vom Original, sonst `SEARXNG_BASE_URL` |
| `knowledge_graph_enabled` | `true` |
| `cache_enabled` | `true` |
| `mcp_enabled` | `true` |

Die Kopien tragen `skills_json = {"auto_assigned": true, "selection_reason": "…"}`.
Bei jedem neuen Lauf werden die vorherigen automatischen Kopien des Projekts gelöscht,
es entstehen also keine Duplikate.

!!! note "Ausfallsicherheit"
    Antwortet der LLM nicht oder liefert unbrauchbares JSON, greift eine deterministische
    Heuristik (Begriffsüberlappung Motion ↔ Agentenprofil). Ein Debattenstart scheitert
    nie an der Auswahl. Liefert der LLM zu wenige gültige Treffer, füllt die Heuristik auf.

## API

```
POST /api/v2/projects                       # agent_selection_mode, auto_agent_count
PATCH /api/v2/projects/{id}
POST /api/v2/projects/{id}/suggest-agents            # Vorschau (nichts wird verändert)
POST /api/v2/projects/{id}/suggest-agents?apply=true # Auswahl anlegen
POST /api/v2/projects/{id}/suggest-agents?count=6    # Anzahl übersteuern
```

Im Auto-Modus führt auch `POST /debates/{id}/start` die Auswahl aus — jeweils frisch
für die aktuelle Motion.

Beispielantwort:

```json
{
  "rationale": "Kombiniert KI-Sicherheitsforschung mit klassischer Pflichtethik …",
  "selection": [
    {"name": "Stuart Russell", "reason": "Liefert die technische und ethische Argumentation zum Kontrollproblem.", "is_global": true},
    {"name": "Immanuel Kant", "reason": "Deontologische Gegenposition zur menschlichen Verantwortung.", "is_global": true}
  ]
}
```

## Felder am Projekt

| Feld | Werte | Default |
|------|-------|---------|
| `agent_selection_mode` | `manual` \| `auto` | `manual` |
| `auto_agent_count` | 2–60 | 4 |

## Dashboard

Im Projekt-Dialog steht ein Umschalter **„🧭 Auswahl der Teilnehmer"**. Bei
*Automatisch* verschwindet die Agenten-Checkliste, stattdessen erscheint die
Teilnehmerzahl und — nach dem Speichern — der Button **„🔮 Auswahl jetzt vorschauen"**,
der die Auswahl samt Begründung je Agent anzeigt, ohne etwas zu verändern.
