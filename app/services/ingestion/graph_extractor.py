"""
FalkorDB Cloud — Cross-Reference Dependency Graph
==================================================
Replaces the in-memory NetworkX DiGraph with a persistent FalkorDB Cloud graph.

Changes from the old implementation:
  - Nodes and edges survive server restarts (FalkorDB cloud persistence)
  - Cross-document edges are fully supported via MERGE on (doc, node_id)
  - `build_graph` is additive: calling it for a new document does NOT reset
    edges from previous documents
  - `link_cross_doc_ref` creates REFERENCES edges across document boundaries

Node schema:
  (:Clause {id: str, doc: str, workspace_id: str, text: str})

Edge schema:
  (:Clause)-[:REFERENCES {edge_type: str}]->(:Clause)
"""

import asyncio
import json
import os
import re
import ssl
from typing import Dict, List, Optional

from falkordb import FalkorDB
from groq import AsyncGroq

from app.redis_client import acquire_llm_slot, create_redis_pool, release_llm_slot

FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.environ.get("FALKORDB_PASSWORD", "")
FALKORDB_SSL = os.environ.get("FALKORDB_SSL", "true").lower() == "true"
FALKORDB_GRAPH = os.environ.get("FALKORDB_GRAPH", "legal_rag")


def _get_connection_params() -> dict:
    """Return keyword-args dict for FalkorDB() constructor."""
    params = {
        "host": FALKORDB_HOST,
        "port": FALKORDB_PORT,
        "password": FALKORDB_PASSWORD,
    }
    if FALKORDB_SSL:
        params["ssl"] = True
        params["ssl_cert_reqs"] = ssl.CERT_NONE
    return params


class DependencyGraph:
    """
    Persistent cross-reference graph backed by FalkorDB Cloud.

    Public API is backward-compatible with the old NetworkX version so that
    graph_expansion.py and executor.py require no changes.
    """

    def __init__(self):
        self._graph = None
        self._connect()

    @staticmethod
    def _result_to_dicts(result) -> List[Dict]:
        """
        Convert a FalkorDB query result to a list of dicts.
        Each dict maps column name → value.
        """
        if not result or not result.result_set:
            return []
        columns = result.header
        rows = result.result_set
        return [dict(zip(columns, row)) for row in rows]

    def _connect(self):
        try:
            db = FalkorDB(**_get_connection_params())
            self._graph = db.select_graph(FALKORDB_GRAPH)
            # Smoke-test connectivity
            self._graph.query("RETURN 1")
            self._ensure_indexes()
            print("✅ Connected to FalkorDB Cloud.")
        except Exception as e:
            print(f"⚠️  FalkorDB connection failed: {e}. Graph features will be disabled.")
            self._graph = None

    def _ensure_indexes(self):
        """Create uniqueness constraint once on Clause.id + doc pair."""
        cypher = (
            "CREATE CONSTRAINT clause_unique IF NOT EXISTS "
            "FOR (c:Clause) REQUIRE (c.id, c.doc, c.workspace_id) IS UNIQUE"
        )
        self._graph.query(cypher)

    def close(self):
        """No-op for backward compatibility with callers that close Neo4j drivers."""
        pass

    # ------------------------------------------------------------------
    # Build graph (additive — call once per ingested document)
    # ------------------------------------------------------------------

    def build_graph(
        self,
        enriched_chunks: List[Dict],
        doc_name: str = "",
        workspace_id: str = "",
    ):
        """
        MERGE clause nodes and intra-document REFERENCES edges.
        Safe to call multiple times — MERGE prevents duplicates.
        """
        if not self._graph:
            return

        for chunk in enriched_chunks:
            node_id = " > ".join(chunk.get("hierarchy", [])) or "Unknown"
            text = chunk.get("text", "")
            doc = doc_name or chunk.get("document_name", "Unknown")

            # Upsert clause node
            self._graph.query(
                """
                MERGE (c:Clause {id: $id, doc: $doc, workspace_id: $workspace_id})
                SET c.text = $text
                """,
                {
                    "id": node_id,
                    "doc": doc,
                    "workspace_id": workspace_id,
                    "text": text,
                },
            )

            # Intra-document dependency edges
            for dep in chunk.get("dependencies_clauses", []):
                self._graph.query(
                    """
                    MERGE (src:Clause {id: $src_id, doc: $doc, workspace_id: $workspace_id})
                    MERGE (tgt:Clause {id: $tgt_id, doc: $doc, workspace_id: $workspace_id})
                    MERGE (src)-[:REFERENCES {edge_type: 'intra_doc'}]->(tgt)
                    """,
                    {
                        "src_id": node_id,
                        "tgt_id": dep,
                        "doc": doc,
                        "workspace_id": workspace_id,
                    },
                )

        print(f"[Graph] Merged clauses from '{doc_name}' into FalkorDB graph.")

    # ------------------------------------------------------------------
    # Cross-document edge creation
    # ------------------------------------------------------------------

    def link_cross_doc_ref(
        self,
        from_node_id: str,
        from_doc: str,
        to_node_id: str,
        to_doc: str,
        edge_type: str = "cross_doc",
        workspace_id: str = "",
    ):
        """
        Creates a REFERENCES edge across two documents.
        Called by reference_parser.py when an LLM resolves a cross-doc ref.
        """
        if not self._graph:
            return
        self._graph.query(
            """
            MERGE (src:Clause {id: $from_id, doc: $from_doc, workspace_id: $workspace_id})
            MERGE (tgt:Clause {id: $to_id, doc: $to_doc, workspace_id: $workspace_id})
            MERGE (src)-[:REFERENCES {edge_type: $edge_type}]->(tgt)
            """,
            {
                "from_id": from_node_id,
                "from_doc": from_doc,
                "to_id": to_node_id,
                "to_doc": to_doc,
                "edge_type": edge_type,
                "workspace_id": workspace_id,
            },
        )

    # ------------------------------------------------------------------
    # Query: dependency chains (Cypher BFS up to 5 hops)
    # ------------------------------------------------------------------

    def get_dependency_chains(
        self,
        node_id: str,
        max_hops: int = 2,
        workspace_id: str = "",
    ) -> List[Dict]:
        """
        Returns the target node and all clauses it directly or indirectly
        references (up to max_hops), across document boundaries.

        Return format: [{"node": str, "text": str, "source_document": str}]
        """
        if not self._graph:
            return []

        result = self._graph.query(
            """
            MATCH (start:Clause {id: $id, workspace_id: $workspace_id})
            OPTIONAL MATCH path = (start)-[:REFERENCES*0..$hops]->(dep:Clause)
            WHERE dep.workspace_id = $workspace_id
            RETURN DISTINCT dep.id AS node, dep.text AS text, dep.doc AS source_document
            """,
            {
                "id": node_id,
                "hops": max_hops,
                "workspace_id": workspace_id,
            },
        )

        records = self._result_to_dicts(result)

        # Filter nulls (unmatched OPTIONAL) and deduplicate
        seen = set()
        chain = []
        for r in records:
            if r.get("node") and r["node"] not in seen:
                seen.add(r["node"])
                chain.append(
                    {
                        "node": r["node"],
                        "text": r.get("text", ""),
                        "source_document": r.get("source_document", "Unknown"),
                    }
                )
        return chain

    # ------------------------------------------------------------------
    # Stats for /ingest response
    # ------------------------------------------------------------------

    def number_of_nodes(self) -> int:
        if not self._graph:
            return 0
        result = self._graph.query("MATCH (c:Clause) RETURN count(c) AS n")
        return result.result_set[0][0]

    def number_of_edges(self) -> int:
        if not self._graph:
            return 0
        result = self._graph.query("MATCH ()-[r:REFERENCES]->() RETURN count(r) AS n")
        return result.result_set[0][0]

    # ------------------------------------------------------------------
    # Backward-compat: old code accessed graph.graph.number_of_nodes()
    # ------------------------------------------------------------------

    @property
    def graph(self):
        return self  # self already implements number_of_nodes / number_of_edges


"""
Cross-Reference Parser — Regex Pre-pass + LLM Fallback
=======================================================
Strategy:
  1. Regex pre-pass catches standard legal reference patterns deterministically
     (Section X.X, clause X, Article XIV, subsection (a)) — zero LLM cost.
  2. If regex finds references, the LLM call is SKIPPED for that chunk.
  3. If regex finds nothing, the LLM call runs to catch natural-language
     cross-document references ("as set forth in the Master Agreement").

This halves LLM token cost on typical legal documents where ~60% of
cross-references follow standard patterns.
"""


_REFERENCE_PATTERNS: List[re.Pattern] = [
    re.compile(
        r"\bSection[s]?\s+\d+(?:\.\d+)*(?:\([a-z]\))?(?:\([ivx]+\))?", re.IGNORECASE
    ),
    re.compile(r"\bArticle[s]?\s+[IVXLCDM\d]+", re.IGNORECASE),
    re.compile(r"\bClause[s]?\s+\d+(?:\.\d+)*(?:\([a-z]\))?", re.IGNORECASE),
    re.compile(r"\bSubsection[s]?\s+\([a-z]\)", re.IGNORECASE),
    re.compile(r"\bParagraph[s]?\s+\d+(?:\.\d+)*", re.IGNORECASE),
    re.compile(r"\bExhibit\s+[A-Z]\b", re.IGNORECASE),
    re.compile(r"\bSchedule\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bAnnex\s+[A-Z\d]+\b", re.IGNORECASE),
]


def _regex_extract_references(text: str) -> List[str]:
    """Deterministic extraction. Returns normalised reference strings."""
    found = []
    for pattern in _REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            ref = match.group(0).strip()
            # Normalise: collapse whitespace
            ref = re.sub(r"\s+", " ", ref)
            found.append(ref)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for r in found:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


class LLMReferenceParser:
    """
    Hybrid cross-reference extractor.
    Regex pre-pass → LLM fallback for natural-language references only.
    """

    def __init__(self):
        self.client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))
        self.model = "llama-3.3-70b-versatile"

    async def resolve_references(
        self,
        chunks: List[Dict],
        doc_name: str = "",
        graph=None,
        org_id: str = "",
        workspace_id: str = "",
    ) -> List[Dict]:
        """
        Enriches each chunk with a `dependencies_clauses` list.
        Uses regex first; only calls LLM if regex finds nothing.
        Uses asyncio.gather with chunk-level concurrency controlled by Redis semaphore.
        """
        regex_hits = 0
        llm_hits = 0
        llm_skips = 0
        cross_doc_edges = 0

        _CROSS_DOC_SIGNALS = re.compile(
            r"\b(master
