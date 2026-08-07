"""Async debate orchestrator with per-agent LLM selection, configurable moderator, and JSON logging.

Kernprinzipien (Fixes gegen das "Kreisdrehen" nach 1/3 der Debatte):
- Agenten sehen den GESAMTEN Debattenverlauf: kompakte Kernpunkt-Historie plus
  die letzten Turns im Volltext — keine Amnesie mehr.
- Wiederholungs-Erkennung vergleicht nur gegen ein begrenztes Fenster und
  prueft den Korrektur-Versuch erneut, statt ihn blind zu akzeptieren.
- Moderator-Korrekturen werden als eigene Turns in den Verlauf eingespeist und
  landen damit tatsaechlich im Kontext der Agenten; bei Themenverengung wird
  ein neuer Fokus gesetzt, der in jeden Folgeprompt einfliesst.
- Hochgeladenes Projekt-Material (Dokumente/Bilder) wird pro Turn semantisch
  abgerufen und den Agenten als zitierfaehige Quelle bereitgestellt.
- Der RESEARCHER erhaelt echte Websuche statt halluzinierter "Quellen".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator

from config import settings
from services.document_service import get_document_index
from services.llm_client import LLMClient, LLMResponse, Message
from services.memory_service import DebateTurn, MemoryService, TurnBuilder
from services.search_service import SearchService
from services.state_manager import StateManager

logger = logging.getLogger(__name__)

MODERATOR_NAME = "moderator"

# Kontext-Aufbau
CONTEXT_WINDOW_TURNS = 6          # letzte N Turns im Volltext
CONTEXT_TURN_MAX_CHARS = 2000     # Volltext-Turns werden hierauf gekappt
HISTORY_SNIPPET_CHARS = 240       # Kernpunkt-Zeile pro aelterem Turn
MODERATOR_CONTEXT_MAX_CHARS = 6000

# Wiederholungs-Erkennung
REPETITION_LOOKBACK_TURNS = 8
REPETITION_NGRAM_SIZE = 5
REPETITION_MIN_OVERLAP = 3
REPETITION_OVERLAP_RATIO = 0.25

# Sampling-Penalties (moderat — hohe Werte erzeugen inkohaerenten Text)
DEFAULT_PRESENCE_PENALTY = 0.3
DEFAULT_FREQUENCY_PENALTY = 0.3
RETRY_PRESENCE_PENALTY = 0.8
RETRY_FREQUENCY_PENALTY = 0.8

# Material-Retrieval
MATERIAL_TOP_K = 4
MATERIAL_CHUNK_MAX_CHARS = 800

# Loop-Detection: erst ab so vielen Turns aussagekraeftig
LOOP_DETECTION_MIN_TURNS = 6
LOOP_MIN_UNIQUE_CONCEPTS = 3

# Token-Budgets. Grosszuegig bemessen, weil Reasoning-Modelle (qwen3.6, deepseek-r1)
# einen erheblichen Teil des Budgets fuer die Gedankenkette verbrauchen, bevor
# ueberhaupt Antworttext entsteht — bei zu kleinem Limit kommt content leer zurueck.
MODERATOR_MAX_TOKENS = 2048
SYNTHESIS_MAX_TOKENS = 8192


# --------------------------------------------------------------------------- #
# Agent roster — default role definitions (used when no custom project exists) #
# --------------------------------------------------------------------------- #

class DefaultAgentRole(str, Enum):
    THESIS      = "thesis"
    ANTI_THESIS = "anti_thesis"
    SYNTHESIZER = "synthesizer"
    FACILITATOR = "facilitator"
    RESEARCHER  = "researcher"


ROLE_PROMPTS: dict[DefaultAgentRole, str] = {
    DefaultAgentRole.THESIS: textwrap.dedent("""\
        You are the THESIS agent. Your goal is to argue IN FAVOR of the motion.
        Present structured arguments, cite evidence, and respond directly to counter-arguments.
        Stay focused on building a coherent case. Do not repeat arguments already made by your
        allies — advance the discussion with new angles or deeper analysis."""),

    DefaultAgentRole.ANTI_THESIS: textwrap.dedent("""\
        You are the ANTI-THESIS agent. Your goal is to argue AGAINST the motion.
        Identify weaknesses in the opposing case, present counter-evidence, and propose
        alternative framings. Be rigorous but fair — strong opposition elevates the debate."""),

    DefaultAgentRole.SYNTHESIZER: textwrap.dedent("""\
        You are the SYNTHESIZER agent. Your goal is to find common ground between opposing positions.
        Identify partial agreements, propose integrative solutions, and highlight where both sides
        may be partially correct. Map the space of possible consensus."""),

    DefaultAgentRole.FACILITATOR: textwrap.dedent("""\
        You are the FACILITATOR agent. Your role is to keep the debate productive:
        clarify ambiguities, ask follow-up questions, and ensure each participant has addressed
        prior points. Do not introduce new arguments — steer the existing ones."""),

    DefaultAgentRole.RESEARCHER: textwrap.dedent("""\
        You are the RESEARCHER agent. Your role is to bring in verified external facts.
        Use ONLY the web search results and project material provided in your context as sources —
        never invent citations. State what the sources say, name them, and note the confidence level.
        Distinguish between peer-reviewed evidence, journalistic reporting, and speculation."""),
}

DEFAULT_MODERATOR_PROMPT = textwrap.dedent("""\
You are the Moderator of this philosophical debate.
Target Goal: {goal}
Motion: {motion}

Review the debate history below and answer with a JSON object:
- `status`: one of "CONTINUE", "CONSENSUS", "CORRECTION"
- `reason`: brief explanation
- `message`: (only if CORRECTION) sharp prompt to redirect drifting agents towards the target goal
- `direction`: suggested new direction topic (null if continuing or consensus)

Only declare CONSENSUS if the participants have genuinely converged across several turns,
not merely because the most recent speaker sounded conciliatory.

Debate history:
{context}""")

SYNTHESIS_PROMPT = textwrap.dedent("""\
Du bist der neutrale AUSWERTER einer abgeschlossenen Multi-Agenten-Debatte.
Dir liegen vor: die Motion, die Teilnehmerliste, das Transkript (kompakt), Graph-Daten
und semantische Highlights. Erstelle daraus ein Markdown-Dokument mit EXAKT dieser Struktur:

# Debatten-Auswertung

## Zusammenfassung
Fasse den Debattenverlauf chronologisch und thematisch zusammen: Hauptargumente beider
Seiten, Wendepunkte, Moderator-Eingriffe, genutzte Quellen (Projekt-Material, Websuche).

## Kernargumente
- **Pro (These):** wichtigste Argumente mit kurzer Stärkeeinschätzung
- **Contra (Antithese):** wichtigste Argumente mit kurzer Stärkeeinschätzung
- **Synthese/Konsensfelder:** wo Annäherung stattfand

## Fazit
Ziehe ein begründetes Gesamtfazit: Was ist das Ergebnis der Debatte? Welche Position steht
am Ende stärker da und warum? Wo blieb Dissens bestehen? Sei ehrlich, wenn kein klares
Ergebnis erreicht wurde.

## Bewertung
### Erschöpfungsgrad der Diskussion: X/10
Ersetze X durch eine Zahl von 1-10. Begründe: Welche relevanten Aspekte der Motion wurden
abgedeckt, welche fehlen (empirische Evidenz, Gegenbeispiele, ethische, historische oder
praktische Dimension)? Wurde in die Tiefe argumentiert oder nur an der Oberfläche?

### Plausibilität des Ergebnisses: X/10
Ersetze X durch eine Zahl von 1-10. Begründe: Wie gut ist das Fazit durch Argumente und
Quellen gestützt? Welche unbelegten Annahmen oder logischen Sprünge schwächen es?
Wäre ein Fachpublikum von diesem Ergebnis überzeugt?

### Qualität der Quellennutzung: X/10
Ersetze X durch eine Zahl von 1-10. Wurden bereitgestelltes Projekt-Material und
Suchergebnisse tatsächlich zitiert und korrekt eingeordnet — oder wurde frei behauptet?

## Offene Fragen
Konkrete Anschlussfragen für weitere Recherche oder eine Folgedebatte.

--- EINGABEDATEN (nicht in die Ausgabe kopieren) ---

MOTION: {motion}

TEILNEHMER: {agents}

TRANSKRIPT (kompakt):
{transcript}

GRAPH-DATEN: {graph}

SEMANTISCHE HIGHLIGHTS: {semantic_highlights}""")

SYNTHESIS_TRANSCRIPT_MAX_CHARS = 12000


# --------------------------------------------------------------------------- #
# Session state                                                               #
# --------------------------------------------------------------------------- #

@dataclass
class DebateSessionState:
    session_id: str
    motion: str
    agents: list[str] = field(default_factory=list)  # agent names or role values
    turns: list[tuple[str, str]] = field(default_factory=list)  # (agent_name, content)
    status: str = "ACTIVE"


@dataclass
class ModeratorConfig:
    goal: str = "Synthesize viewpoints and resolve contradictions towards a consensus."
    system_prompt: str = ""
    interval_turns: int = 3
    drift_threshold: float = 0.5


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #

class DebateOrchestrator:
    """Central async loop with per-agent LLM clients, configurable moderator, JSON logs."""

    def __init__(
        self,
        session_id: str,
        motion: str,
        *,
        agents_config: dict[str, dict[str, Any]] | None = None,  # name → {provider, base_url, model, temperature, system_prompt}
        moderator_cfg: ModeratorConfig | None = None,
        project_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.motion = motion
        self.project_id = project_id
        self.current_focus: str = ""  # vom Moderator gesetzte Debatten-Richtung
        self.state = DebateSessionState(session_id=session_id, motion=motion)
        self.moderator_cfg = moderator_cfg or ModeratorConfig()
        self._agents_config = agents_config or {}
        self._last_turn_id: str | None = None

        # Resolve per-agent LLM clients
        self._agent_clients: dict[str, LLMClient] = {}

        if self._agents_config:
            self.state.agents = list(self._agents_config.keys())
            for agent_name, cfg in self._agents_config.items():
                client = LLMClient(
                    provider=cfg.get("provider", settings.default_provider),
                    model=cfg.get("model") or getattr(settings, f"{settings.default_provider}_model", "gpt-4o-mini"),
                    base_url=cfg.get("base_url", ""),
                    api_key=cfg.get("api_key", getattr(settings, "openai_api_key", "")),
                    temperature=cfg.get("temperature", 0.7),
                )
                self._agent_clients[agent_name] = client
        else:
            # Single default LLM (backward compat)
            provider = settings.default_provider
            model = getattr(settings, f"{provider}_model")
            base_url = "" if provider == "openai" else getattr(settings, "ollama_base_url", "")
            api_key = getattr(settings, "openai_api_key", "")
            default_client = LLMClient(
                provider=provider, model=model, base_url=base_url,
                api_key=api_key, temperature=settings.default_temperature,
            )
            self._agent_clients["default"] = default_client

        # Log path for JSON persistence (Fallback fuer lokale Laeufe ohne /data)
        self.log_path = self._resolve_log_path(session_id)

        # Other services — alle strikt pro Session gescoped
        self.memory = MemoryService(
            chroma_persist_dir=settings.chroma_persist_dir,
            neo4j_uri=settings.neo4j_uri,
            neo4j_user=settings.neo4j_user,
            neo4j_password=settings.neo4j_password,
            session_id=session_id,
        )
        self.search = SearchService(provider="searxng", searxng_url=settings.searxng_base_url)
        self.state_mgr = StateManager(settings.valkey_url, session_id=session_id)

    @staticmethod
    def _resolve_log_path(session_id: str) -> Path:
        # Konfigurierter Pfad zuerst, lokaler Fallback fuer Laeufe ohne /data-Mount
        for base in (Path(settings.debate_log_dir), Path("./data/debate-logs").resolve()):
            try:
                path = base / session_id / "turns.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                return path
            except (PermissionError, OSError):
                continue
        raise RuntimeError("Kein beschreibbares Log-Verzeichnis fuer Debatten-Logs gefunden")

    async def initialize(self) -> None:
        await self.memory.initialize()
        await self.state_mgr.initialize()
        logger.info("Debate engine initialized — session %s", self.session_id)

    async def close(self) -> None:
        await self.memory.close()
        await self.search.close()
        await self.state_mgr.close()

    # -- Core debate loop -----------------------------------------------------

    async def run_debate(
        self,
        max_rounds: int = 15,
        max_duration_minutes: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Main async rotation loop. Yields each agent turn as streaming text."""
        await self.state_mgr.activate_debate()
        logger.info("Starting debate on: %s (max_rounds=%s, max_duration_minutes=%s)", self.motion, max_rounds, max_duration_minutes)
        start_time = time.time()

        if not self.state.agents:
            default_roles = [r.value for r in DefaultAgentRole]
            self.state.agents = default_roles[:2]  # thesis + anti_thesis by default

        num_agents = max(len(self.state.agents), 1)
        turn_counter = 0

        try:
            while True:
                turn_counter += 1
                round_num = (turn_counter - 1) // num_agents + 1

                # Time limit check
                if max_duration_minutes and (time.time() - start_time) / 60.0 >= max_duration_minutes:
                    yield f"[SESSION TERMINATED: Zeitlimit von {max_duration_minutes} Minuten erreicht]"
                    self.state.status = "TERMINATED"
                    break

                # Max rounds limit check (eine Runde = jeder Agent einmal)
                if round_num > max_rounds:
                    yield f"[SESSION TERMINATED: Rundenlimit von {max_rounds} Runden erreicht]"
                    self.state.status = "TERMINATED"
                    break

                # Kill-switch
                active = await self.state_mgr.is_active()
                if not active:
                    yield "[SESSION TERMINATED: kill-switch engaged]"
                    self.state.status = "TERMINATED"
                    break

                # Cost guardrail (session-scoped)
                current_cost = await self.state_mgr.get_total_cost()
                if current_cost >= settings.cost_threshold_usd:
                    yield f"[SESSION TERMINATED: cost guardrail triggered (${current_cost:.4f})]"
                    self.state.status = "TERMINATED"
                    break

                # Select next agent round-robin
                agent_name = self.state.agents[(turn_counter - 1) % num_agents]
                self.state.status = "ACTIVE"
                llm_client = self._agent_clients.get(agent_name, list(self._agent_clients.values())[0])

                # Vollstaendiger Debattenkontext + Material + Research
                context = self._build_context()
                material_block = await self._get_material_block()
                research_block = await self._get_research_block(agent_name)

                system_prompt = self._build_system_prompt(agent_name)
                user_prompt = self._build_user_prompt(agent_name, context, material_block, research_block)

                messages = [
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_prompt),
                ]

                llm_resp = await self._call_llm(
                    llm_client, messages,
                    presence_penalty=DEFAULT_PRESENCE_PENALTY,
                    frequency_penalty=DEFAULT_FREQUENCY_PENALTY,
                )
                turn_text = llm_resp.text

                # Wiederholungs-Check (nur gegen juengste Turns) mit erneuter Pruefung
                if self._detect_repetition(turn_text):
                    logger.warning("Repetition detected for %s — re-rolling turn", agent_name)
                    yield f"[MODERATOR HINWEIS]: Die Äußerung von {agent_name} wiederholte Bekanntes — neuer Impuls wird angefordert."

                    correction_msg = [
                        Message(role="system", content=system_prompt + (
                            "\n\nWARNUNG: Dein vorheriger Entwurf wiederholte bereits Gesagtes. "
                            "Antworte jetzt mit einem inhaltlich NEUEN Beitrag: neuer Aspekt, neues Beispiel oder neue Schlussfolgerung."
                        )),
                        Message(role="user", content=user_prompt),
                    ]
                    retry_resp = await self._call_llm(
                        llm_client, correction_msg,
                        presence_penalty=RETRY_PRESENCE_PENALTY,
                        frequency_penalty=RETRY_FREQUENCY_PENALTY,
                    )
                    if not self._detect_repetition(retry_resp.text):
                        llm_resp = retry_resp
                        turn_text = retry_resp.text
                    else:
                        # Zweiter Versuch ebenfalls repetitiv: besseren der beiden behalten,
                        # aber nicht endlos re-rollen (Kosten!)
                        logger.warning("Retry for %s still repetitive — keeping retry text", agent_name)
                        llm_resp = retry_resp
                        turn_text = retry_resp.text

                # Track cost/tokens
                await self.state_mgr.add_cost(llm_resp.usage.cost_usd)
                await self.state_mgr.add_tokens(llm_resp.usage.total_tokens)
                total_turns = await self.state_mgr.increment_turn_counter()

                # Persist to memory layers (mit echter Rundenzahl und RESPONDS_TO-Kette)
                debate_turn = TurnBuilder(agent_name=agent_name, round_num=round_num).build(turn_text)
                await self.memory.add_turn(debate_turn)
                await self.memory.persist_turn(debate_turn, parent_id=self._last_turn_id)
                self._last_turn_id = debate_turn.turn_id
                self.state.turns.append((agent_name, turn_text))

                # JSON log
                self._write_log_entry(agent_name, turn_text, llm_resp.usage)

                yield f"[{agent_name.upper()}]: {turn_text}"

                # Periodic moderator evaluation & active oversight
                if total_turns % self.moderator_cfg.interval_turns == 0:
                    async for event in self._moderate(total_turns, num_agents):
                        yield event
                    if self.state.status == "SUCCESS":
                        break

                # Loop detection via Neo4j (session-scoped, erst ab genuegend Turns)
                if turn_counter % (self.moderator_cfg.interval_turns * 2) == 0:
                    loop_event = await self._check_for_loop(round_num)
                    if loop_event:
                        yield loop_event

                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            logger.info("Debate session %s cancelled gracefully", self.session_id)
        finally:
            self.state.status = "TERMINATED" if self.state.status != "SUCCESS" else "SUCCESS"
            yield "[SYNTHESIS PHASE INITIATED]"
            try:
                syn_result = await self._run_synthesis()
            except Exception as exc:
                logger.error("Synthese fuer Session %s fehlgeschlagen: %s", self.session_id, exc)
                syn_result = f"[Auswertung fehlgeschlagen: {exc}]"
            self._write_log_entry(MODERATOR_NAME, syn_result, kind="synthesis")
            yield f"[SYNTHESIS]: {syn_result}"

    # -- Moderation -------------------------------------------------------------

    async def _moderate(self, total_turns: int, num_agents: int) -> AsyncGenerator[str, None]:
        """Moderator-Evaluation, deren Ergebnis TATSAECHLICH in die Debatte zurueckfliesst."""
        mod_result = await self._evaluate_moderator()
        mod_status = mod_result.get("status", "CONTINUE")
        direction = mod_result.get("direction")
        correction = mod_result.get("correction")
        steering = direction or correction or mod_result.get("reason") or "Diskussion weiter vertiefen."
        self._write_log_entry(
            MODERATOR_NAME, f"Status: {mod_status} — Steuerung: {steering}", kind="moderator",
        )
        yield f"[MODERATOR EVALUATION]: Status: {mod_status} — Steuerung: {steering}"

        # Konsens erst gueltig, wenn jeder Agent mehrfach zu Wort kam
        if mod_status == "CONSENSUS":
            if total_turns >= num_agents * 2:
                yield "[CONSENSUS REACHED: Moderator stellt Konsens fest]"
                self.state.status = "SUCCESS"
                return
            yield "[MODERATOR HINWEIS]: Konsens-Meldung verworfen — Debatte ist noch zu jung."

        if correction:
            # Korrektur als eigener Turn → landet im Kontext der naechsten Agenten
            self.state.turns.append((MODERATOR_NAME, f"KORREKTUR AN ALLE: {correction}"))
            self._write_log_entry(MODERATOR_NAME, f"KORREKTUR AN ALLE: {correction}", kind="moderator")
            yield f"[MODERATOR CORRECTION]: {correction}"

        if direction:
            self.current_focus = str(direction)

    async def _check_for_loop(self, round_num: int) -> str | None:
        """Erkennt Themenverengung und setzt einen NEUEN Fokus, statt nur zu warnen."""
        loop_info = await self.memory.detect_loop(round_num)
        turn_count = loop_info.get("turn_count", 0)
        unique = loop_info.get("unique_concepts", LOOP_MIN_UNIQUE_CONCEPTS + 1)
        if turn_count < LOOP_DETECTION_MIN_TURNS or unique >= LOOP_MIN_UNIQUE_CONCEPTS:
            return None

        frequent = loop_info.get("frequent_concepts") or []
        stale = ", ".join(frequent[:5]) if frequent else "den bisherigen Begriffen"
        self.current_focus = (
            f"Die Diskussion kreist um {stale}. Eroeffne einen NEUEN Aspekt der Motion, "
            "der bisher nicht behandelt wurde (z.B. andere Disziplin, Gegenbeispiel, Praxisfolgen)."
        )
        self.state.turns.append((MODERATOR_NAME, f"KURSWECHSEL: {self.current_focus}"))
        self._write_log_entry(MODERATOR_NAME, f"KURSWECHSEL: {self.current_focus}", kind="moderator")
        return f"[MODERATOR ESKALATION]: Diskussion dreht sich im Kreis — neuer Fokus gesetzt: {self.current_focus}"

    # -- Prompt building --------------------------------------------------------

    def _build_system_prompt(self, agent_name: str) -> str:
        anti_repetition_rule = (
            "\n\nSTRIKTE REGELN FÜR DIESE RUNDE:\n"
            "1. WIEDERHOLUNGSVERBOT: Die Kernpunkt-Historie im Kontext zeigt, was bereits gesagt wurde — "
            "wiederhole nichts davon, sondern baue darauf auf oder widersprich mit neuen Argumenten.\n"
            f"2. PERSÖNLICHKEIT: Du bist EXKLUSIV {agent_name}. Verfalle NIEMALS in die Rolle anderer Persönlichkeiten.\n"
            "3. REAKTION: Reagiere DIREKT auf den letzten Redner und beachte Anweisungen des Moderators.\n"
            "4. QUELLEN: Wenn Projekt-Material oder Suchergebnisse bereitgestellt sind, zitiere sie mit Dateiname bzw. Quelle."
        )
        default_prompts = {r.value: p for r, p in ROLE_PROMPTS.items()}
        if self._agents_config and agent_name in self._agents_config:
            base = self._agents_config[agent_name].get(
                "system_prompt",
                f"You are {agent_name}, a philosophical debate agent analyzing the motion.",
            )
        elif agent_name in default_prompts:
            base = default_prompts[agent_name]
        else:
            base = f"You are {agent_name}, participating in this debate."
        return base + anti_repetition_rule

    def _build_user_prompt(self, agent_name: str, context: str, material_block: str, research_block: str) -> str:
        parts = [f"DEBATTEN-THEMA (MOTION): {self.motion}"]
        if self.current_focus:
            parts.append(f"AKTUELLE MODERATOR-VORGABE: {self.current_focus}")
        if material_block:
            parts.append(material_block)
        if research_block:
            parts.append(research_block)
        parts.append(context)
        parts.append(
            f"DEINE AUFGABE ALS {agent_name.upper()}: Antworte auf den letzten Redner mit neuen Erkenntnissen "
            "und beziehe bereitgestelltes Material ein, wo es die Argumentation stuetzt."
        )
        return "\n\n".join(parts)

    def _build_context(self) -> str:
        """Voller Debattenkontext: Kernpunkt-Historie + letzte Turns im Volltext."""
        if not self.state.turns:
            return "Es gab bisher keine Vorredner. Eröffne die Debatte mit deiner Eingangsthese."

        parts: list[str] = []
        older = self.state.turns[:-CONTEXT_WINDOW_TURNS]
        if older:
            lines = [f"- [{name}]: {self._snippet(text)}" for name, text in older]
            parts.append(
                "--- KERNPUNKT-HISTORIE (bereits gesagt — NICHT wiederholen) ---\n" + "\n".join(lines)
            )

        recent = self.state.turns[-CONTEXT_WINDOW_TURNS:]
        recent_parts = [
            f"--- VORHERIGER REDNER: [{name}] ---\n{text[:CONTEXT_TURN_MAX_CHARS]}"
            for name, text in recent
        ]
        parts.append("\n\n".join(recent_parts))
        return "\n\n".join(parts)

    @staticmethod
    def _snippet(text: str) -> str:
        """Erster Satz bzw. gekappter Anfang eines Turns als Kernpunkt-Zeile."""
        clean = re.sub(r"[#*_`>]+", "", text).strip().replace("\n", " ")
        match = re.match(r"(.{40,%d}?[.!?])\s" % HISTORY_SNIPPET_CHARS, clean)
        if match:
            return match.group(1)
        return clean[:HISTORY_SNIPPET_CHARS] + ("…" if len(clean) > HISTORY_SNIPPET_CHARS else "")

    # -- Material & Research ----------------------------------------------------

    async def _get_material_block(self) -> str:
        """Relevante Chunks aus hochgeladenem Projekt-Material fuer den aktuellen Stand."""
        if not self.project_id:
            return ""
        query = self.motion
        if self.state.turns:
            query += "\n" + self.state.turns[-1][1][:500]
        records = await get_document_index().search(self.project_id, query, top_k=MATERIAL_TOP_K)
        if not records:
            return ""
        lines = ["--- PROJEKT-MATERIAL (hochgeladene Quellen, zitiere den Dateinamen) ---"]
        for rec in records:
            fname = rec.get("filename", "unbekannt")
            kind = rec.get("kind", "document")
            prefix = "BILD" if kind == "image" else "DOKUMENT"
            lines.append(f"[{prefix}: {fname}]\n{str(rec.get('document', ''))[:MATERIAL_CHUNK_MAX_CHARS]}")
        return "\n\n".join(lines)

    async def _get_research_block(self, agent_name: str) -> str:
        """Echte Websuche fuer den RESEARCHER — keine erfundenen Quellen mehr."""
        if agent_name != DefaultAgentRole.RESEARCHER.value:
            return ""
        query = self.motion
        if self.state.turns:
            query = f"{self.motion} {self._snippet(self.state.turns[-1][1])[:120]}"
        try:
            results = await self.search.search(query, max_results=5)
        except Exception as exc:
            logger.warning("Websuche fuer Researcher fehlgeschlagen: %s", exc)
            return ""
        if not results:
            return ""
        lines = ["--- WEB-SUCHERGEBNISSE (nutze NUR diese als externe Quellen) ---"]
        for idx, r in enumerate(results, start=1):
            lines.append(f"[{idx}] {r.title}\n    {r.snippet}\n    Quelle: {r.url}")
        return "\n".join(lines)

    # -- Internal helpers ------------------------------------------------------

    def _detect_repetition(self, new_text: str) -> bool:
        """N-Gramm-Vergleich NUR gegen die juengsten Turns — kumulative Historie
        wuerde die Trefferwahrscheinlichkeit mit jeder Runde unfair erhoehen."""
        if not self.state.turns or not new_text.strip():
            return False

        def normalize(text: str) -> str:
            # Markdown-Struktur (Ueberschriften, Listen, Betonung) nicht mitzaehlen
            text = re.sub(r"^#{1,6}\s.*$", " ", text, flags=re.MULTILINE)
            return re.sub(r"[*_`>#\-]+", " ", text)

        def get_ngrams(text: str, n: int = REPETITION_NGRAM_SIZE) -> set[str]:
            words = [w.lower().strip(".,!?:;\"'()[]{}") for w in normalize(text).split() if len(w) > 2]
            if len(words) < n:
                return set()
            return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}

        new_ngrams = get_ngrams(new_text)
        if not new_ngrams:
            return False

        recent_turns = [
            (name, text) for name, text in self.state.turns[-REPETITION_LOOKBACK_TURNS:]
            if name != MODERATOR_NAME
        ]
        for _, past_text in recent_turns:
            past_ngrams = get_ngrams(past_text)
            if not past_ngrams:
                continue
            overlap = new_ngrams.intersection(past_ngrams)
            if len(overlap) >= REPETITION_MIN_OVERLAP or (
                len(new_ngrams) > 4 and len(overlap) / len(new_ngrams) > REPETITION_OVERLAP_RATIO
            ):
                return True
        return False

    async def _call_llm(
        self,
        client: LLMClient,
        messages: list[Message],
        presence_penalty: float = DEFAULT_PRESENCE_PENALTY,
        frequency_penalty: float = DEFAULT_FREQUENCY_PENALTY,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # Der LLMClient hat bereits eine eigene Retry-Schleife — hier nur ein
        # zweiter Versuch als letzte Absicherung, sonst multiplizieren sich Retries.
        last_exc = None
        for attempt in (1, 2):
            try:
                return await client.chat(
                    messages,
                    max_tokens=max_tokens,
                    presence_penalty=presence_penalty,
                    frequency_penalty=frequency_penalty,
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LLM call failed for session %s (attempt %d/2): %s",
                    self.session_id, attempt, exc,
                )
                if attempt == 1:
                    await asyncio.sleep(10.0)
        raise RuntimeError(f"LLM retries exhausted for session {self.session_id}") from last_exc

    async def _evaluate_moderator(self) -> dict[str, Any]:
        context = self._build_moderator_context()
        sys_prompt = self.moderator_cfg.system_prompt or DEFAULT_MODERATOR_PROMPT
        goal_text = self.moderator_cfg.goal or "Synthesize viewpoints and resolve contradictions towards a consensus."
        prompt = sys_prompt.format(context=context, motion=self.motion, goal=goal_text)
        messages = [
            Message(role="system", content="You are an objective debate moderator. Respond with JSON only."),
            Message(role="user", content=prompt),
        ]
        target_client = list(self._agent_clients.values())[0]
        resp = await self._call_llm(
            target_client, messages,
            presence_penalty=0.0, frequency_penalty=0.0, max_tokens=MODERATOR_MAX_TOKENS,
        )
        return self._parse_moderator_json(resp.text)

    def _build_moderator_context(self) -> str:
        """Der Moderator sieht die GANZE Debatte kompakt, nicht nur 2 Turns."""
        if not self.state.turns:
            return "Noch keine Beitraege."
        lines = [f"[{name}]: {self._snippet(text)}" for name, text in self.state.turns[:-3]]
        recent = [f"[{name}] (Volltext): {text[:1200]}" for name, text in self.state.turns[-3:]]
        context = "\n".join(lines + recent)
        return context[-MODERATOR_CONTEXT_MAX_CHARS:]

    @staticmethod
    def _parse_moderator_json(text: str) -> dict[str, Any]:
        clean = text.strip()
        for fence in ("```json", "```"):
            clean = clean.replace(fence, "").strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            data = {"status": "CONTINUE", "reason": "Could not parse moderator JSON"}
        return {
            "status": data.get("status", "CONTINUE"),
            "reason": data.get("reason", ""),
            "correction": data.get("message"),
            "direction": data.get("direction"),
            "summary": f"Status: {data.get('status', 'UNKNOWN')}",
        }

    async def _run_synthesis(self) -> str:
        logger.info("Triggering synthesis for session %s", self.session_id)
        graph_data = await self.memory.synthesize()
        highlights = await self.memory.semantic_search(self.motion, top_k=10)

        prompt = SYNTHESIS_PROMPT.format(
            motion=self.motion, agents=json.dumps(list(self._agent_clients.keys()), indent=2),
            transcript=self._build_synthesis_transcript(),
            graph=graph_data, semantic_highlights=json.dumps(highlights[:5], indent=2, default=str),
        )
        messages = [
            Message(role="system", content=prompt),
            Message(role="user", content=(
                f"MOTION: {self.motion}\n\n"
                "Erstelle jetzt die vollständige Debatten-Auswertung mit Zusammenfassung, "
                "Fazit und Bewertung (Erschöpfungsgrad, Plausibilität, Quellennutzung) "
                "gemäß der vorgegebenen Struktur."
            )),
        ]
        target_client = list(self._agent_clients.values())[0]
        resp = await self._call_llm(
            target_client, messages,
            presence_penalty=0.0, frequency_penalty=0.0, max_tokens=SYNTHESIS_MAX_TOKENS,
        )
        logger.info("Synthesis complete — %d characters generated", len(resp.text))
        return resp.text

    def _build_synthesis_transcript(self) -> str:
        """Kompaktes Volltranskript als Bewertungsgrundlage fuer die Auswertung."""
        if not self.state.turns:
            return "Keine Beitraege — die Debatte wurde vor dem ersten Turn beendet."
        lines = []
        for idx, (name, text) in enumerate(self.state.turns, start=1):
            clean = text.strip().replace("\n", " ")
            lines.append(f"{idx}. [{name}]: {clean[:600]}")
        transcript = "\n".join(lines)
        if len(transcript) > SYNTHESIS_TRANSCRIPT_MAX_CHARS:
            # Anfang und Ende behalten — Mitte kuerzen
            head = transcript[: SYNTHESIS_TRANSCRIPT_MAX_CHARS // 2]
            tail = transcript[-SYNTHESIS_TRANSCRIPT_MAX_CHARS // 2:]
            transcript = f"{head}\n[… Mitte gekuerzt …]\n{tail}"
        return transcript

    def _write_log_entry(self, agent_name: str, content: str, usage=None, kind: str = "turn") -> None:
        """Append a JSON line to the debate log file.

        ``kind`` unterscheidet Redebeitraege ("turn") von Moderator-Eingriffen
        ("moderator") und der Abschlussauswertung ("synthesis"). Ohne diese
        Persistenz waeren Moderation und Auswertung nach Sessionende verloren —
        sie gingen bisher nur an verbundene WebSockets.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "agent": agent_name,
            "content": content,
            "usage": {
                "total_tokens": getattr(usage, "total_tokens", 0),
                "cost_usd": getattr(usage, "cost_usd", 0.0),
            },
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- Public API -------------------------------------------------------------

    async def get_session_status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "motion": self.motion,
            "status": self.state.status,
            "turns_completed": len(self.state.turns),
            "agents": list(self.state.agents),
            "active_on_valkey": await self.state_mgr.is_active(),
            "total_cost_usd": await self.state_mgr.get_total_cost(),
            "total_tokens": await self.state_mgr.get_total_tokens(),
            "json_log_path": str(self.log_path),
        }

    async def add_agent(self, agent_name: str) -> None:
        if agent_name not in self.state.agents:
            self.state.agents.append(agent_name)
            logger.info("Added agent %s to session", agent_name)

    async def remove_agent(self, agent_name: str) -> None:
        if agent_name in self.state.agents:
            self.state.agents.remove(agent_name)
            logger.info("Removed agent %s from session", agent_name)
