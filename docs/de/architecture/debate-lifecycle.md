# Debatten-Lebenszyklus

Eine Debatte durchläuft drei Phasen: Vorbereitung, Rotation und Auswertung. Der
gesamte Ablauf liegt in `engine/orchestrator.py::run_debate()`.

## Gesamtablauf

```mermaid
stateDiagram-v2
    [*] --> Vorbereitung
    Vorbereitung --> Rotation: Agenten konfiguriert
    
    state Rotation {
        [*] --> Abbruchprüfung
        Abbruchprüfung --> Kontextaufbau: alle Limits ok
        Kontextaufbau --> LLMAufruf
        LLMAufruf --> Wiederholungsprüfung
        Wiederholungsprüfung --> Neuversuch: Wiederholung erkannt
        Neuversuch --> Persistenz
        Wiederholungsprüfung --> Persistenz: Beitrag neu
        Persistenz --> Moderation: alle N Turns
        Persistenz --> Loopprüfung: alle 2N Turns
        Moderation --> Abbruchprüfung
        Loopprüfung --> Abbruchprüfung
        Persistenz --> Abbruchprüfung
    }

    Rotation --> Auswertung: Limit, Konsens oder Stopp
    Auswertung --> [*]

    note right of Abbruchprüfung
        Zeit, Runden, Kill-Switch, Kosten
    end note
    note right of Auswertung
        Läuft im finally-Block —
        auch nach Abbruch
    end note
```

## Ein einzelner Turn im Detail

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant V as Valkey
    participant D as DocumentIndex
    participant W as SearXNG
    participant L as LLM
    participant M as Memory
    participant F as JSONL-Log

    O->>V: is_active() + get_total_cost()
    V-->>O: Limits ok
    O->>O: Agent per Round-Robin wählen
    O->>O: _build_context()<br/>Kernpunkt-Historie + letzte 6 Turns
    O->>D: search(project_id, Motion + letzter Beitrag)
    D-->>O: Top-4 Materialausschnitte
    opt Agent ist RESEARCHER
        O->>W: Websuche zum Thema
        W-->>O: Quellen mit URL
    end
    O->>L: chat(System-Prompt, Kontext + Material)
    L-->>O: Redebeitrag
    O->>O: _detect_repetition() gegen letzte 8 Turns
    alt Wiederholung erkannt
        O->>L: Neuversuch mit höheren Penalties
        L-->>O: neuer Beitrag (wird erneut geprüft)
    end
    O->>V: Kosten + Tokens + Turn-Zähler
    O->>M: ChromaDB-Embedding + Neo4j-Knoten
    O->>F: JSONL-Zeile (kind = turn)
    O-->>O: yield an WebSocket-Abonnenten
```

## Kontextaufbau — der Kern gegen das Kreisdrehen

Frühere Versionen gaben den Agenten nur die letzten zwei Beiträge. Bei mehr als
zwei Teilnehmern sah ein Agent damit nicht einmal seine eigene letzte Aussage —
und wiederholte sich zwangsläufig.

```mermaid
flowchart LR
    A["Alle bisherigen Turns"] --> B{"Position?"}
    B -->|"älter als<br/>die letzten 6"| C["Kernpunkt-Historie<br/>ein Satz je Beitrag"]
    B -->|"letzte 6"| D["Volltext<br/>max. 2000 Zeichen"]
    C --> E["Prompt"]
    D --> E
    F["Moderator-Korrekturen<br/>als eigene Turns"] --> E
    G["Projekt-Material<br/>Top-4 Chunks"] --> E
    H["Aktueller Fokus<br/>vom Moderator"] --> E
```

Entscheidend ist, dass **Moderator-Korrekturen als eigene Turns** in `state.turns`
landen. Vorher wurden sie nur an den WebSocket gestreamt und erreichten keinen
einzigen Agenten — die Moderation war wirkungslos.

## Moderation

Der Moderator läuft alle `interval_turns` Beiträge und sieht die **gesamte** Debatte
(ältere Beiträge verdichtet, die letzten drei im Volltext).

```mermaid
flowchart TD
    A["Moderator-Evaluation"] --> B{Status}
    B -->|CONSENSUS| C{"Genug Turns?<br/>≥ 2× Teilnehmerzahl"}
    C -->|ja| D["Debatte beenden<br/>Status SUCCESS"]
    C -->|nein| E["Meldung verwerfen<br/>Debatte läuft weiter"]
    B -->|CORRECTION| F["Korrektur als Turn<br/>in den Verlauf einspeisen"]
    B -->|CONTINUE| G["direction → current_focus"]
    F --> H["Fließt in jeden Folgeprompt"]
    G --> H
```

Die Konsens-Sperre verhindert, dass ein einzelner versöhnlicher Beitrag die Debatte
vorzeitig beendet.

## Loop-Erkennung

Alle `2 × interval_turns` Beiträge fragt der Orchestrator den Diskursgraphen ab:
Wie viele **verschiedene** Konzepte wurden in dieser Session genannt?

```mermaid
flowchart LR
    A["Neo4j-Abfrage<br/>MATCH DebateTurn -MENTIONS-> Concept"] --> B{"Turns ≥ 6<br/>und<br/>Konzepte < 3?"}
    B -->|nein| C["Alles in Ordnung"]
    B -->|ja| D["Neuen Fokus setzen"]
    D --> E["KURSWECHSEL als Turn<br/>in den Verlauf"]
```

Die Mindestanzahl von sechs Turns ist wichtig: Ohne sie würde die Prüfung bei jeder
jungen Debatte anschlagen, weil noch kaum Konzepte erfasst sind.

## Abbruchbedingungen

| Bedingung | Prüfung | Ergebnis |
|-----------|---------|----------|
| Zeitlimit | `max_duration_minutes` | `TERMINATED` |
| Rundenlimit | `max_rounds` (eine Runde = jeder Agent einmal) | `TERMINATED` |
| Kill-Switch | Valkey `debate:{id}:status:active` | `TERMINATED` |
| Kostenbremse | Valkey-Summe ≥ `COST_THRESHOLD_USD` | `TERMINATED` |
| Konsens | Moderator meldet `CONSENSUS` | `SUCCESS` |

In **allen** Fällen läuft anschließend die Auswertung — sie steht im `finally`-Block.

## Auswertung

```mermaid
flowchart TD
    A["Debattenende"] --> B["Transkript verdichten<br/>max. 12.000 Zeichen"]
    A --> C["Neo4j-Graph der Session"]
    A --> D["ChromaDB Top-10 Highlights"]
    B --> E["Auswertungs-Prompt"]
    C --> E
    D --> E
    E --> F["LLM, 8192 Tokens"]
    F --> G["Markdown-Dokument"]
    G --> H["JSONL kind = synthesis"]
    G --> I["WebSocket-Stream"]
```

Das Ergebnis enthält Zusammenfassung, Kernargumente, Fazit, drei Bewertungen
(Erschöpfungsgrad, Plausibilität, Quellennutzung — je 1–10 mit Begründung) und
offene Fragen. Details unter [Personas & Auswertung](../personas-und-auswertung.md).

## Persistenz der Ereignisse

Alle Ereignisse landen in `{DEBATE_LOG_DIR}/{session_id}/turns.jsonl`:

| `kind` | Inhalt |
|--------|--------|
| `turn` | Redebeitrag eines Agenten |
| `moderator` | Evaluation, Korrektur oder Kurswechsel |
| `synthesis` | Abschlussauswertung |

!!! danger "Früher gingen Moderation und Auswertung verloren"
    Bis zum Fix wurden nur `turn`-Einträge geschrieben. Die Auswertung ging
    ausschließlich an verbundene WebSockets und wurde im Log auf 120 Zeichen gekürzt.
    War niemand verbunden, war das Ergebnis unwiederbringlich weg.
    `POST /debates/{id}/evaluate` erzeugt eine fehlende Auswertung aus den
    gespeicherten Beiträgen neu.
