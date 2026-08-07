"""KI-gestuetzte Auswahl der zum Debattenthema passenden Agenten.

Der Auswahl-LLM bekommt einen kompakten Katalog aller verfuegbaren Agenten
(eigene plus global freigegebene) und waehlt daraus die fachlich geeignetsten
fuer die Motion aus — mit Begruendung und dem Ziel, gegensaetzliche Positionen
abzudecken statt nur Zustimmung zu erzeugen.

Faellt der LLM aus oder liefert unbrauchbares JSON, greift eine deterministische
Heuristik (Begriffsueberlappung zwischen Motion und Agentenprofil), damit ein
Debattenstart nie an der Auswahl scheitert.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from services.llm_client import LLMClient, Message

logger = logging.getLogger(__name__)

CATALOG_BIO_CHARS = 220
MIN_AGENTS = 2
MAX_AGENTS = 12
SELECTION_MAX_TOKENS = 3000

SELECTION_PROMPT = """\
Du stellst das Teilnehmerfeld einer wissenschaftlichen Debatte zusammen.

MOTION (Debattenthema):
{motion}

VERFÜGBARE AGENTEN (Nummer, Name, Fachgebiet, Kurzprofil):
{catalog}

AUFGABE: Wähle GENAU {count} Agenten aus, die dieses Thema am fruchtbarsten diskutieren.

Auswahlkriterien, in dieser Reihenfolge:
1. Fachliche Nähe zum Thema — wer hat inhaltlich wirklich etwas beizutragen?
2. Gegensätzlichkeit — wähle Perspektiven, die sich reiben (Theorie gegen Praxis,
   Optimismus gegen Skepsis). Ein Feld aus lauter Gleichgesinnten erzeugt keine Debatte.
3. Methodische Breite — unterschiedliche Disziplinen und Herangehensweisen.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt in genau dieser Form:
{{"selection": [{{"number": <Nummer aus dem Katalog>, "reason": "<ein Satz, warum dieser Agent>"}}],
  "rationale": "<ein bis zwei Sätze zur Gesamtzusammenstellung>"}}"""


@dataclass
class AgentCandidate:
    """Ein zur Auswahl stehender Agent (DB-unabhaengig, damit testbar)."""
    id: str
    name: str
    field: str = ""
    bio: str = ""

    @property
    def profile_text(self) -> str:
        return f"{self.name} {self.field} {self.bio}"


@dataclass
class AgentSelection:
    candidate: AgentCandidate
    reason: str


def build_catalog(candidates: Iterable[AgentCandidate]) -> str:
    """Kompakter, nummerierter Katalog fuer den Auswahl-Prompt."""
    lines = []
    for idx, c in enumerate(candidates, start=1):
        bio = re.sub(r"\s+", " ", c.bio or "").strip()[:CATALOG_BIO_CHARS]
        field = f" [{c.field}]" if c.field else ""
        lines.append(f"{idx}. {c.name}{field}: {bio}")
    return "\n".join(lines)


def parse_selection(text: str, candidates: list[AgentCandidate], count: int) -> tuple[list[AgentSelection], str]:
    """Extrahiert die Auswahl aus der LLM-Antwort. Leere Liste, wenn unbrauchbar."""
    clean = text.strip()
    for fence in ("```json", "```"):
        clean = clean.replace(fence, "")
    # Erstes JSON-Objekt aus moeglicherweise umgebendem Text herausschneiden
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end <= start:
        return [], ""
    try:
        data = json.loads(clean[start:end + 1])
    except json.JSONDecodeError:
        return [], ""

    picks: list[AgentSelection] = []
    seen: set[str] = set()
    for item in data.get("selection") or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        if not (1 <= number <= len(candidates)):
            continue
        cand = candidates[number - 1]
        if cand.id in seen:
            continue
        seen.add(cand.id)
        picks.append(AgentSelection(candidate=cand, reason=str(item.get("reason", "")).strip()))
        if len(picks) >= count:
            break
    return picks, str(data.get("rationale", "")).strip()


_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{5,}")
_GENERIC = {
    "debatte", "thema", "frage", "agent", "wissenschaft", "forschung",
    "verbindung", "bereich", "modell", "modelle", "system", "systeme",
}


def heuristic_selection(motion: str, candidates: list[AgentCandidate], count: int) -> list[AgentSelection]:
    """Deterministischer Fallback: Begriffsueberlappung Motion ↔ Agentenprofil."""
    motion_words = {w.lower() for w in _WORD_RE.findall(motion)} - _GENERIC
    scored: list[tuple[int, int, AgentCandidate]] = []
    for idx, c in enumerate(candidates):
        profile = {w.lower() for w in _WORD_RE.findall(c.profile_text)}
        # idx als Tiebreaker haelt die Reihenfolge stabil und reproduzierbar
        scored.append((len(motion_words & profile), -idx, c))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [
        AgentSelection(candidate=c, reason="Automatisch nach Begriffsüberlappung gewählt (LLM-Auswahl nicht verfügbar).")
        for _, _, c in scored[:count]
    ]


async def select_agents_for_motion(
    motion: str,
    candidates: list[AgentCandidate],
    llm_client: LLMClient | None,
    count: int = 4,
) -> tuple[list[AgentSelection], str]:
    """Waehlt die passendsten Agenten zur Motion. Liefert (Auswahl, Begruendung)."""
    if not candidates:
        return [], "Keine Agenten verfügbar."

    count = max(MIN_AGENTS, min(int(count or 4), MAX_AGENTS, len(candidates)))

    if llm_client is None:
        logger.warning("Kein LLM fuer die Agentenauswahl verfuegbar — nutze Heuristik")
        return heuristic_selection(motion, candidates, count), "Heuristische Auswahl (kein LLM konfiguriert)."

    prompt = SELECTION_PROMPT.format(motion=motion, catalog=build_catalog(candidates), count=count)
    messages = [
        Message(role="system", content="Du bist ein neutraler Kurator wissenschaftlicher Debatten. Antworte ausschliesslich mit JSON."),
        Message(role="user", content=prompt),
    ]
    try:
        resp = await llm_client.chat(
            messages, max_tokens=SELECTION_MAX_TOKENS, presence_penalty=0.0, frequency_penalty=0.0
        )
        picks, rationale = parse_selection(resp.text, candidates, count)
    except Exception as exc:
        logger.warning("LLM-Agentenauswahl fehlgeschlagen: %s — nutze Heuristik", exc)
        picks, rationale = [], ""

    if not picks:
        return heuristic_selection(motion, candidates, count), rationale or "Heuristische Auswahl (LLM-Antwort unbrauchbar)."

    # Auffuellen, falls das LLM zu wenige gueltige Nummern lieferte
    if len(picks) < count:
        chosen = {p.candidate.id for p in picks}
        for extra in heuristic_selection(motion, candidates, len(candidates)):
            if extra.candidate.id not in chosen:
                picks.append(extra)
                chosen.add(extra.candidate.id)
            if len(picks) >= count:
                break

    return picks[:count], rationale
