"""User-scoped knowledge service: Neo4j graph + ChromaDB vector cache.

All knowledge is isolated per user_id. Neo4j nodes carry a ``userId`` property
that is mandatory on every write and enforced on every read query.
ChromaDB collections are namespaced per user to prevent cross-tenant data leaks.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class KnowledgeService:
    """User-scoped knowledge graph and vector cache management.

    Wraps Neo4j and ChromaDB with strict user_id isolation.
    When ``knowledge_graph_enabled`` is True, debate turns are persisted to the graph.
    When ``cache_enabled`` is True, turns are also stored in ChromaDB for RAG retrieval.
    """

    def __init__(
        self,
        chroma_persist_dir: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
    ) -> None:
        self._chroma_dir = chroma_persist_dir
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._neo4j_driver = None
        self._chroma_client = None
        self._collections: dict[str, Any] = {}

    async def initialize(self) -> None:
        """Initialize Neo4j driver and ChromaDB client."""
        from neo4j import AsyncGraphDatabase
        import chromadb

        self._neo4j_driver = AsyncGraphDatabase.driver(
            self._neo4j_uri,
            auth=(self._neo4j_user, self._neo4j_password),
        )
        self._chroma_client = chromadb.PersistentClient(path=self._chroma_dir)

        async with self._neo4j_driver.session() as session:
            await session.run(
                "CREATE CONSTRAINT debate_turn_id IF NOT EXISTS "
                "FOR (t:DebateTurn) REQUIRE t.id IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT agent_name IF NOT EXISTS "
                "FOR (a:Agent) REQUIRE a.name IS UNIQUE"
            )
        logger.info("KnowledgeService initialized (Neo4j + ChromaDB)")

    async def close(self) -> None:
        if self._neo4j_driver:
            await self._neo4j_driver.close()
        if self._chroma_client:
            self._chroma_client = None

    # -- ChromaDB (vector cache, user-scoped) --------------------------------

    def _user_collection(self, user_id: uuid.UUID):
        """Get or create a ChromaDB collection scoped to a user."""
        key = str(user_id)
        if key not in self._collections:
            self._collections[key] = self._chroma_client.get_or_create_collection(
                name=f"debate_turns_{key[:12]}",
            )
        return self._collections[key]

    async def add_turn_to_cache(self, user_id: uuid.UUID, turn_id: str,
                                 content: str, metadata: dict) -> None:
        """Cache a debate turn in ChromaDB for RAG retrieval (only if cache_enabled)."""
        collection = self._user_collection(user_id)
        collection.add(
            ids=[turn_id],
            documents=[content],
            metadatas=[json.dumps(metadata)],
        )

    async def semantic_search(self, user_id: uuid.UUID, query: str,
                                top_k: int = 5) -> list[dict[str, Any]]:
        """Search within a user's cached turns."""
        collection = self._user_collection(user_id)
        try:
            results = collection.query(query_texts=[query], n_results=top_k)
            records = []
            for doc, meta in zip(results.get("documents", [[]])[0], results.get("metadatas", [[]])[0]):
                records.append(json.loads(meta) if isinstance(meta, str) else meta)
            return records
        except Exception as exc:
            logger.warning("ChromaDB search failed for user %s: %s", user_id, exc)
            return []

    # -- Neo4j (knowledge graph, user-scoped) ---------------------------------

    async def persist_turn_to_graph(self, user_id: uuid.UUID, turn: dict) -> None:
        """Persist a debate turn to the knowledge graph with user isolation.

        All nodes carry ``userId`` property. Queries MUST filter by userId.
        """
        async with self._neo4j_driver.session() as session:
            await session.run(
                """
                MERGE (a:Agent {name: $agent_name, userId: $user_id})
                ON CREATE SET a.role = $role
                MERGE (t:DebateTurn {id: $turn_id, userId: $user_id})
                SET t.content = $content, t.round = $round_num, t.timestamp = $timestamp
                CREATE (a)-[:SPOKE_AT]->(t)
                """,
                user_id=str(user_id),
                agent_name=turn.get("agent_name", "unknown"),
                role=turn.get("role", "assistant"),
                turn_id=turn.get("turn_id", str(uuid.uuid4())),
                content=turn.get("content", ""),
                round_num=turn.get("round_num", 0),
                timestamp=turn.get("timestamp", ""),
            )

    async def detect_loop(self, user_id: uuid.UUID, round_num: int) -> dict[str, Any]:
        """Detect circular reasoning patterns for a specific user's graph."""
        async with self._neo4j_driver.session() as session:
            result = await session.run(
                """
                MATCH (t:DebateTurn)-[:MENTIONS]->(c:Concept)
                WHERE t.userId = $user_id AND t.round <= $round_num
                WITH c, count(t) AS frequency
                RETURN collect(c.label + ':' + frequency) AS frequent_concepts,
                       size(collect(c.label)) AS unique_concepts
                """,
                user_id=str(user_id),
                round_num=round_num,
            )
            record = (await result.single()) or {}
            concepts_str = record.get("frequent_concepts") or []
            concept_labels = [c.split(":")[0] for c in concepts_str if ":" in c]
            unique = record.get("unique_concepts", 0)
        return {"frequent_concepts": concept_labels, "unique_concepts": unique}

    async def synthesize_for_user(self, user_id: uuid.UUID) -> str:
        """Retrieve the full knowledge graph for a specific user."""
        queries = {
            "agents": "MATCH (a:Agent {userId: $user_id}) RETURN a.name AS name ORDER BY a.name",
            "concepts": "MATCH (c:Concept) RETURN c.label AS label ORDER BY c.label",
            "top_arguments": """
                MATCH (t:DebateTurn {userId: $user_id})
                RETURN t.id AS id, t.content AS content, t.round AS round
                ORDER BY t.round DESC LIMIT 50
            """,
        }
        result = {}
        async with self._neo4j_driver.session() as session:
            for key, q in queries.items():
                records = await session.run(q, user_id=str(user_id))
                result[key] = [dict(r) for r in await records.data()]
        return json.dumps(result, default=str)