"""Dual-layer memory: ChromaDB (vector) + Neo4j (graph) via Cypher.

Alle Reads/Writes sind pro Debatten-Session gescoped (``session_id``), damit
Loop-Detection und Synthese nicht Daten fremder Sessions mitzaehlen.
ChromaDB-Aufrufe (sync) laufen via ``asyncio.to_thread``, um den Event-Loop
nicht zu blockieren.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import chromadb
from neo4j import AsyncGraphDatabase

logger = logging.getLogger(__name__)

# Woerter, die trotz Grossschreibung keine Konzepte sind (Satzanfaenge, Fuellwoerter)
_CONCEPT_STOPWORDS = {
    "aber", "auch", "dabei", "daher", "dann", "darum", "dass", "dennoch",
    "denn", "der", "die", "das", "dem", "den", "dies", "diese", "dieser",
    "dieses", "doch", "eine", "einer", "eines", "einem", "einen", "hier",
    "jedoch", "somit", "wenn", "weil", "wir", "sie", "ich", "und", "oder",
    "the", "this", "that", "with", "from", "have", "has", "your", "you",
    "moderator", "these", "antithese", "synthese", "argument", "punkt",
}

_MIN_CONCEPT_LENGTH = 5
_MAX_CONCEPTS_PER_TURN = 20


def extract_concepts(text: str) -> list[str]:
    """Extract candidate concept labels from (German) debate text.

    Deutsche Substantive sind grossgeschrieben — wir nehmen kapitalisierte
    Woerter ab einer Mindestlaenge, ohne Stopwoerter und Markdown-Reste.
    """
    words = re.findall(r"\b[A-ZÄÖÜ][a-zäöüß]{%d,}\b" % (_MIN_CONCEPT_LENGTH - 1), text)
    seen: dict[str, None] = {}
    for w in words:
        lw = w.lower()
        if lw not in _CONCEPT_STOPWORDS and w not in seen:
            seen[w] = None
    return list(seen)[:_MAX_CONCEPTS_PER_TURN]


@dataclass
class DebateTurn:
    turn_id: str
    agent_name: str
    role: str
    content: str
    round_num: int
    timestamp: str
    metadata_: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "agent_name": self.agent_name,
            "role": self.role,
            "content": self.content,
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            **self.metadata_,
        }


class MemoryService:
    """Manages ChromaDB collection for semantic search and Neo4j for graph storage."""

    def __init__(
        self,
        chroma_persist_dir: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        session_id: str = "",
    ) -> None:
        self.chroma_persist_dir = chroma_persist_dir
        self.session_id = session_id or "default"
        # ChromaDB persistent client — uses SQLite under the hood
        self.chroma_client = chromadb.PersistentClient(path=chroma_persist_dir)
        # Collection name must be a valid identifier; debate-turns is safe on all platforms
        self.collection = self.chroma_client.get_or_create_collection(name="debate_turns")

        self.neo4j_driver = AsyncGraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password),
        )

    # ---- Lifecycle -----------------------------------------------------------

    async def initialize(self) -> None:
        """Ensure Neo4j indexes and constraints exist."""
        async with self.neo4j_driver.session() as session:
            # Create constraints idempotently
            await session.run("CREATE CONSTRAINT debate_turn_id IF NOT EXISTS FOR (t:DebateTurn) REQUIRE t.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT agent_name IF NOT EXISTS FOR (a:Agent) REQUIRE a.name IS UNIQUE")
            await session.run("CREATE CONSTRAINT concept_label IF NOT EXISTS FOR (c:Concept) REQUIRE c.label IS UNIQUE")
            await session.run("CREATE INDEX debate_turn_session IF NOT EXISTS FOR (t:DebateTurn) ON (t.sessionId)")
        logger.info("Neo4j schema initialized")

    async def close(self) -> None:
        # chromadb.PersistentClient besitzt kein close(); Referenz freigeben genuegt
        self.collection = None  # type: ignore[assignment]
        self.chroma_client = None  # type: ignore[assignment]
        await self.neo4j_driver.close()

    # ---- ChromaDB (vector layer) ---------------------------------------------

    async def add_turn(self, turn: DebateTurn) -> None:
        """Embed and store a debate turn in ChromaDB for RAG."""
        try:
            meta = {
                "turn_id": str(turn.turn_id),
                "agent_name": str(turn.agent_name),
                "role": str(turn.role),
                "round_num": int(turn.round_num),
                "timestamp": str(turn.timestamp),
                "session_id": self.session_id,
            }
            await asyncio.to_thread(
                self.collection.add,
                ids=[turn.turn_id],
                documents=[turn.content],
                metadatas=[meta],
            )
            logger.debug("ChromaDB added turn %s", turn.turn_id)
        except Exception as exc:
            logger.warning("ChromaDB add_turn warning: %s", exc)

    async def semantic_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Find semantically similar past arguments within this session."""
        try:
            results = await asyncio.to_thread(
                self.collection.query,
                query_texts=[query],
                n_results=top_k,
                where={"session_id": self.session_id},
            )
            records: list[dict[str, Any]] = []
            if results and results.get("documents") and len(results["documents"]) > 0:
                for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                    rec = dict(meta) if isinstance(meta, dict) else {}
                    rec["document"] = doc
                    records.append(rec)
            return records
        except Exception as exc:
            logger.warning("ChromaDB semantic_search warning: %s", exc)
            return []

    # ---- Neo4j (graph layer) ---------------------------------------------

    async def persist_turn(self, turn: DebateTurn, parent_id: str | None = None) -> None:
        """Store entities and relationships for a debate turn in Neo4j."""
        query = """
        MERGE (a:Agent {name: $agent_name})
        ON CREATE SET a.role = $role
        MERGE (t:DebateTurn {id: $turn_id})
        SET t.content = $content, t.round = $round_num, t.timestamp = $timestamp,
            t.sessionId = $session_id
        CREATE (a)-[:SPOKE_AT]->(t)

        FOREACH (concept IN $concepts |
            MERGE (c:Concept {label: concept})
            CREATE (t)-[:MENTIONS]->(c))

        FOREACH (_ IN CASE WHEN $parent_id IS NOT NULL THEN [1] ELSE [] END |
            MERGE (prev:DebateTurn {id: $parent_id})
            CREATE (t)-[:RESPONDS_TO]->(prev))

        FOREACH (item IN $fact_results |
            MERGE (f:FactCheck {id: $turn_id + ':' + item.action})
            SET f.evidence = item.evidence, f.confidence = item.confidence
            CREATE (t)-[:HAS_FACT_CHECK]->(f))
        """
        try:
            params = self._build_turn_params(turn)
            params["parent_id"] = parent_id
            async with self.neo4j_driver.session() as session:
                await session.run(query, **params)
        except Exception as exc:
            logger.warning("Neo4j persist_turn warning: %s", exc)

    async def detect_loop(self, round_num: int) -> dict[str, Any]:
        """Detect circular reasoning patterns via Cypher density analysis.

        Scoped auf die aktuelle Session; liefert zusaetzlich die Anzahl der
        erfassten Turns, damit der Aufrufer eine leere Konzept-Basis von einer
        echten Themenverengung unterscheiden kann.
        """
        query = """
        MATCH (t:DebateTurn {sessionId: $session_id})
        WITH count(t) AS turn_count
        OPTIONAL MATCH (t:DebateTurn {sessionId: $session_id})-[:MENTIONS]->(c:Concept)
        WHERE t.round <= $round_num
        WITH turn_count, c, count(t) AS frequency
        RETURN turn_count,
               collect(c.label + ':' + toString(frequency)) AS frequent_concepts,
               count(DISTINCT c.label) AS unique_concepts
        """
        try:
            async with self.neo4j_driver.session() as session:
                result = await session.run(query, session_id=self.session_id, round_num=round_num)
                record = (await result.single()) or {}
                concepts_str = record.get("frequent_concepts") or []
                concept_labels = [c.rsplit(":", 1)[0] for c in concepts_str if ":" in c]
                unique = record.get("unique_concepts", 0)
                turn_count = record.get("turn_count", 0)
            return {
                "frequent_concepts": concept_labels,
                "unique_concepts": unique,
                "turn_count": turn_count,
            }
        except Exception as exc:
            logger.warning("Neo4j detect_loop warning: %s", exc)
            return {"frequent_concepts": [], "unique_concepts": 10, "turn_count": 0}

    async def synthesize(self) -> str:
        """Retrieve the session's graph structure for downstream synthesis."""
        queries = {
            "agents": """
                MATCH (a:Agent)-[:SPOKE_AT]->(t:DebateTurn {sessionId: $session_id})
                RETURN DISTINCT a.name AS name, a.role AS role ORDER BY a.name
            """,
            "concepts": """
                MATCH (t:DebateTurn {sessionId: $session_id})-[:MENTIONS]->(c:Concept)
                RETURN DISTINCT c.label AS label ORDER BY c.label
            """,
            "relationships": """
                MATCH (t:DebateTurn {sessionId: $session_id})-[r]->()
                RETURN type(r) AS type, count(*) AS cnt
                ORDER BY cnt DESC
            """,
            "top_arguments": """
                MATCH (t:DebateTurn {sessionId: $session_id})
                RETURN t.id AS id, t.content AS content, t.round AS round
                ORDER BY t.round ASC LIMIT 50
            """,
        }
        result = {}
        try:
            async with self.neo4j_driver.session() as session:
                for key, q in queries.items():
                    res = await session.run(q, session_id=self.session_id)
                    records = await res.data()
                    result[key] = records
        except Exception as exc:
            logger.warning("Neo4j synthesize warning: %s", exc)
        return json.dumps(result, default=str)

    # ---- Internal helpers ----------------------------------------------------

    def _build_turn_params(self, turn: DebateTurn) -> dict[str, Any]:
        return {
            "turn_id": turn.turn_id,
            "agent_name": turn.agent_name,
            "role": turn.role,
            "content": turn.content,
            "round_num": turn.round_num,
            "timestamp": turn.timestamp,
            "session_id": self.session_id,
            "concepts": extract_concepts(turn.content),
            "parent_id": None,
            "fact_results": [],
        }


@dataclass
class TurnBuilder:
    """Helper to construct DebateTurn objects."""
    agent_name: str
    role: str = "assistant"
    round_num: int = 1
    metadata_: dict[str, Any] = field(default_factory=dict)

    def build(self, content: str) -> DebateTurn:
        return DebateTurn(
            turn_id=str(uuid.uuid4()),
            agent_name=self.agent_name,
            role=self.role,
            content=content,
            round_num=self.round_num,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata_=self.metadata_,
        )
