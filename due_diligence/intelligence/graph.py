"""
Neo4j AuraDB — Cross-Reference Dependency Graph
=================================================
Replaces the in-memory NetworkX DiGraph with a persistent Neo4j AuraDB graph.

Changes from the old implementation:
  - Nodes and edges survive server restarts (AuraDB cloud persistence)
  - Cross-document edges are fully supported via MERGE on (doc, node_id)
  - `build_graph` is additive: calling it for a new document does NOT reset
    edges from previous documents
  - `link_cross_doc_ref` creates REFERENCES edges across document boundaries

Node schema:
  (:Clause {id: str, doc: str, text: str})

Edge schema:
  (:Clause)-[:REFERENCES {edge_type: str}]->(:Clause)
"""

import os
from typing import Dict, List, Optional

from neo4j import GraphDatabase, Driver
from dotenv import load_dotenv

load_dotenv()


def _get_driver() -> Driver:
    neo4j_uri = (os.environ.get("NEO4J_URI") or "").strip()
    neo4j_user = (
        os.environ.get("NEO4J_USERNAME")
        or os.environ.get("NEO4J_USER")
        or "neo4j"
    ).strip()
    neo4j_pass = (os.environ.get("NEO4J_PASSWORD") or "").strip()
    if not neo4j_uri:
        raise ValueError("NEO4J_URI is not configured.")
    return GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))


class DependencyGraph:
    """
    Persistent cross-reference graph backed by Neo4j AuraDB.

    Public API is backward-compatible with the old NetworkX version so that
    graph_expansion.py and executor.py require no changes.
    """

    def __init__(self):
        self._driver: Optional[Driver] = None
        self._connect()

    def _connect(self):
        try:
            self._driver = _get_driver()
            self._driver.verify_connectivity()
            self._ensure_indexes()
            print("✅ Connected to Neo4j AuraDB.")
        except Exception as e:
            print(f"⚠️  Neo4j connection failed: {e}. Graph features will be disabled.")
            self._driver = None

    def _ensure_driver(self) -> bool:
        """Best-effort reconnect so graph can recover after transient startup failures."""
        if self._driver is not None:
            return True
        self._connect()
        return self._driver is not None

    def _ensure_indexes(self):
        """Create uniqueness constraint once on Clause.id + doc pair."""
        with self._driver.session() as session:
            session.run(
                "CREATE CONSTRAINT clause_unique IF NOT EXISTS "
                "FOR (c:Clause) REQUIRE (c.id, c.doc) IS UNIQUE"
            )

    def close(self):
        if self._driver:
            self._driver.close()

    # ------------------------------------------------------------------
    # Build graph (additive — call once per ingested document)
    # ------------------------------------------------------------------

    def build_graph(self, enriched_chunks: List[Dict], doc_name: str = ""):
        """
        MERGE clause nodes and intra-document REFERENCES edges.
        Safe to call multiple times — MERGE prevents duplicates.
        """
        if not self._ensure_driver():
            return

        with self._driver.session() as session:
            for chunk in enriched_chunks:
                node_id = " > ".join(chunk.get("hierarchy", [])) or "Unknown"
                text = chunk.get("text", "")
                doc = doc_name or chunk.get("document_name", "Unknown")

                # Upsert clause node
                session.run(
                    """
                    MERGE (c:Clause {id: $id, doc: $doc})
                    SET c.text = $text
                    """,
                    id=node_id, doc=doc, text=text,
                )

                # Intra-document dependency edges
                for dep in chunk.get("dependencies_clauses", []):
                    session.run(
                        """
                        MERGE (src:Clause {id: $src_id, doc: $doc})
                        MERGE (tgt:Clause {id: $tgt_id, doc: $doc})
                        MERGE (src)-[:REFERENCES {edge_type: 'intra_doc'}]->(tgt)
                        """,
                        src_id=node_id, tgt_id=dep, doc=doc,
                    )

        print(f"[Graph] Merged clauses from '{doc_name}' into Neo4j graph.")

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
    ):
        """
        Creates a REFERENCES edge across two documents.
        Called by reference_parser.py when an LLM resolves a cross-doc ref.
        """
        if not self._ensure_driver():
            return
        with self._driver.session() as session:
            session.run(
                """
                MERGE (src:Clause {id: $from_id, doc: $from_doc})
                MERGE (tgt:Clause {id: $to_id, doc: $to_doc})
                MERGE (src)-[:REFERENCES {edge_type: $edge_type}]->(tgt)
                """,
                from_id=from_node_id, from_doc=from_doc,
                to_id=to_node_id, to_doc=to_doc,
                edge_type=edge_type,
            )

    # ------------------------------------------------------------------
    # Query: dependency chains (Cypher BFS up to 5 hops)
    # ------------------------------------------------------------------

    def get_dependency_chains(self, node_id: str, max_hops: int = 5) -> List[Dict]:
        """
        Returns the target node and all clauses it directly or indirectly
        references (up to max_hops), across document boundaries.

        Return format: [{"node": str, "text": str, "source_document": str}]
        """
        if not self._ensure_driver():
            return []

        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (start:Clause {id: $id})
                OPTIONAL MATCH path = (start)-[:REFERENCES*0..$hops]->(dep:Clause)
                RETURN DISTINCT dep.id AS node, dep.text AS text, dep.doc AS source_document
                """,
                id=node_id, hops=max_hops,
            )
            records = result.data()

        # Filter nulls (unmatched OPTIONAL) and deduplicate
        seen = set()
        chain = []
        for r in records:
            if r.get("node") and r["node"] not in seen:
                seen.add(r["node"])
                chain.append({
                    "node": r["node"],
                    "text": r.get("text", ""),
                    "source_document": r.get("source_document", "Unknown"),
                })
        return chain

    # ------------------------------------------------------------------
    # Stats for /ingest response
    # ------------------------------------------------------------------

    def number_of_nodes(self) -> int:
        if not self._ensure_driver():
            return 0
        with self._driver.session() as session:
            r = session.run("MATCH (c:Clause) RETURN count(c) AS n")
            return r.single()["n"]

    def number_of_edges(self) -> int:
        if not self._ensure_driver():
            return 0
        with self._driver.session() as session:
            r = session.run("MATCH ()-[r:REFERENCES]->() RETURN count(r) AS n")
            return r.single()["n"]

    # ------------------------------------------------------------------
    # Backward-compat: old code accessed graph.graph.number_of_nodes()
    # ------------------------------------------------------------------

    @property
    def graph(self):
        return self  # self already implements number_of_nodes / number_of_edges
