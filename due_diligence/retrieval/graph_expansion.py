"""
Graph Expander — Neo4j AuraDB
==============================
Replaces the NetworkX graph_expansion.py.
Traverses the persistent Neo4j graph to pull dependency chains for each hit.
Now propagates source_document for cross-document reference provenance.
"""

from typing import Dict, List


class GraphExpander:
    """
    Takes the direct semantic hits from Qdrant and traverses the Neo4j
    dependency graph to pull in all referenced clauses (intra- and cross-doc).
    """

    def __init__(self, graph_engine, workspace_id: str = ""):
        self.graph = graph_engine  # DependencyGraph (Neo4j) instance
        self.workspace_id = workspace_id

    def expand_context(self, vector_hits: List[Dict]) -> List[Dict]:
        print(f"\n[GraphExpander] Expanding context for {len(vector_hits)} hits...")

        expanded_context: List[Dict] = []
        seen_nodes: set = set()

        for hit in vector_hits:
            node_id = hit.get("node_id") or hit.get("node")
            if not node_id or node_id == "Unknown":
                continue

            # get_dependency_chains now returns {node, text, source_document}
            chain = self.graph.get_dependency_chains(
                node_id, workspace_id=self.workspace_id
            )

            for item in chain:
                curr_node = item.get("node")
                if curr_node and curr_node not in seen_nodes:
                    seen_nodes.add(curr_node)
                    expanded_context.append(
                        {
                            "node": curr_node,
                            "node_id": curr_node,
                            "text": item.get("text", ""),
                            "source_document": item.get("source_document", "Unknown"),
                            "document_name": item.get("source_document", "Unknown"),
                        }
                    )

        print(
            f"  ↳ Expanded {len(vector_hits)} chunks → "
            f"{len(expanded_context)} interconnected nodes "
            f"(including cross-document references)."
        )
        return expanded_context
