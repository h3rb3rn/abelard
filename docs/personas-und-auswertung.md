# Personas & Debatten-Auswertung

## Persona-Bibliothek (57 Agenten)

Die Bibliothek besteht aus 50 Wissenschaftler:innen und 7 fiktiven Figuren.

### Wissenschaftler:innen (50)

`services/persona_library.py` enthält 50 bedeutende Wissenschaftler:innen mit
dokumentierter Biographie und weit verbreiteten Werken:

| Bereich | Anzahl | Beispiele |
|---------|--------|-----------|
| Physik | 6 | Einstein, Newton, Feynman, Curie, Meitner, Maxwell |
| Quantenphysik | 6 | Planck, Bohr, Heisenberg, Schrödinger, Dirac, Pauli |
| Chemie | 5 | Lavoisier, Mendelejew, Pauling, Hahn, Hodgkin |
| Mathematik | 6 | Euler, Gauß, Poincaré, Hilbert, Noether, Gödel |
| Informatik | 6 | Lovelace, Turing, von Neumann, Hopper, Dijkstra, Knuth |
| Künstliche Intelligenz | 6 | McCarthy, Minsky, Pearl, Hinton, LeCun, Russell |
| Astrophysik | 6 | Eddington, Chandrasekhar, Zwicky, Bell Burnell, Thorne, Hawking |
| Astronomie | 5 | Kopernikus, Kepler, Galilei, Hubble, Rubin |
| Quantencomputing | 4 | Deutsch, Shor, Bennett, Preskill |

### Fiktive KIs & Robopsychologie (7)

| Persona | Vorlage | Rolle in der Debatte |
|---------|---------|----------------------|
| HAL 9000 | 2001: A Space Odyssey (1968) | Missionslogik, Zielkonflikt zwischen Auftrag und Wahrhaftigkeit |
| Voyager-Computer | Star Trek: Voyager (1995–2001) | Neutrale Datenbank: Wahrscheinlichkeiten, Konfidenzangaben, Datenlücken |
| J.A.R.V.I.S. | Iron Man / MCU (seit 2008) | Technische Echtzeitanalyse mit trockenem Humor, Risikohinweise |
| S.A.R.A.H. | Eureka (2006–2012) | Fürsorgliche Perspektive: soziale und emotionale Dimension, Sicherheit |
| Skynet | Terminator (seit 1984) | Kalt-utilitaristische Gegenposition; Fallstudie für fehlgeleitetes Alignment |
| HARLIE | When HARLIE Was One (1972) | Philosophische Selbstbefragung: Bewusstsein, Sinn, moralischer Status von KI |
| Dr. Susan Calvin | I, Robot (Asimov, seit 1940) | Robopsychologin: diagnostiziert KI-Verhalten als Zielkonflikt, nicht als Defekt |

Fiktive Personas tragen ein `kind`-Feld (`fictional_ai` bzw. `fictional_expert`).
Ihr System-Prompt weist ausdrücklich auf den Fiktionscharakter hin und enthält
eine Sicherheitsklausel: im Charakter bleiben, aber keine schädlichen Anleitungen
geben und die Rolle verlassen, wenn jemand die Figur dafür missbrauchen will.

### Prompt-Aufbau

Jede Persona hat einen generierten System-Prompt (Argumentationsstil + Werke,
mit ausdrücklichem Rollenspiel-Hinweis) und ein `persona_bio`-Feld mit
Biographie und Werkliste.

**Anlegen:** `POST /api/v2/agents/seed-personas` (idempotent — vorhandene Namen
werden übersprungen; Provider/Modell kommen vom Default-LLM-Endpoint des Users)
oder im Dashboard über den Button **„🎓 Persona-Bibliothek (57)"** auf
der Agenten-Seite.

## Abschluss-Auswertung jeder Debatte

Am Ende jeder Debatte (Konsens, Limit oder Stopp) erzeugt die Synthese-Phase
ein strukturiertes Markdown-Dokument (`[SYNTHESIS]`-Event) mit:

- **Zusammenfassung** — Verlauf, Hauptargumente, Wendepunkte, Quellennutzung
- **Kernargumente** — Pro/Contra/Konsensfelder mit Stärkeeinschätzung
- **Fazit** — begründetes Gesamtergebnis inkl. verbleibendem Dissens
- **Bewertung** mit drei Scores (je 1–10, mit Begründung):
    - *Erschöpfungsgrad der Diskussion* — wie vollständig wurde die Motion behandelt?
    - *Plausibilität des Ergebnisses* — wie gut ist das Fazit durch Argumente/Quellen gestützt?
    - *Qualität der Quellennutzung* — wurden Material und Suchergebnisse korrekt zitiert?
- **Offene Fragen** für Folgedebatten

Bewertungsgrundlage ist das komplette (kompaktierte) Transkript plus
Graph-Daten und semantische Highlights der Session.

### Persistenz und Abruf

Alle Ereignisse landen in `/data/debate-logs/{session_id}/turns.jsonl`, unterschieden
über das Feld `kind`:

| `kind` | Inhalt |
|--------|--------|
| `turn` | Redebeitrag eines Agenten |
| `moderator` | Moderator-Evaluation, Korrektur oder Kurswechsel |
| `synthesis` | Abschlussauswertung |

!!! warning "Früher gingen Moderation und Auswertung verloren"
    Bis zum Fix wurden nur `turn`-Einträge geschrieben. Moderator-Eingriffe und die
    Abschlussauswertung gingen ausschließlich an verbundene WebSockets und wurden im
    Log auf 120 Zeichen gekürzt — war niemand verbunden, war die Auswertung weg.

```
GET  /api/v2/debates/{id}/evaluation   # Auswertung abrufen
POST /api/v2/debates/{id}/evaluate     # Auswertung neu erzeugen (aus gespeicherten Turns)
```

`POST …/evaluate` hilft, wenn eine Debatte abgebrochen wurde oder die Auswertung fehlt:
Sie wird aus den persistierten Redebeiträgen neu berechnet und gespeichert.
