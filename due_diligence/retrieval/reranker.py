from typing import Dict, List

from sentence_transformers import CrossEncoder


class LegalCrossEncoder:
    """
    Implements Strict Linearization + Cross-Encoder Reranking to solve
    the negation failure modes (e.g., [EXCEPTS], [SUPERSEDES]) in legal Graph RAG.
    """

    def __init__(self):
        # FIX 1: Updated argument to model_name_or_path to remove the warning
        self.encoder = CrossEncoder(model_name_or_path="BAAI/bge-reranker-base")

    def _prune_dead_clauses(self, context_nodes: List[Dict]) -> List[Dict]:
        """
        The Graph Shield: Removes nodes that are explicitly superseded by
        newer nodes in the retrieved context BEFORE reranking.
        """
        superseded_target = set()
        for node in context_nodes:
            if node.get("edge_type") == "SUPERSEDES":
                target = node.get("target_document")
                if target and target != "N/A":
                    target_id = target.split(", ")[1] if ", " in target else target
                    superseded_target.add(target_id)

        pruned_nodes = [
            n for n in context_nodes if n.get("node_id") not in superseded_target
        ]

        pruned_count = len(context_nodes) - len(pruned_nodes)
        if pruned_count > 0:
            print(
                f"  ↳ [Graph Shield] Pruned {pruned_count} superseded clause(s) from context window."
            )
        return pruned_nodes

    def _linearize_context(self, context_item: Dict) -> str:
        """
        Stitches nodes and edges into a deterministic string to preserve
        legal mechanics (negations, exceptions) for the Cross-Encoder.
        """
        node_id = context_item.get("node_id", "Unknown")
        text = context_item.get("text", "")

        edge_type = context_item.get("edge_type", "DEPENDS_ON")
        source_doc = context_item.get("source_document", "Current Document")
        target_doc = context_item.get("target_document", "Base Agreement")
        date_meta = context_item.get("execution_date", "Unknown Date")

        if edge_type != "DEPENDS_ON":
            linearized_string = (
                f"Context: {source_doc} (Executed {date_meta}), Clause {node_id} "
                f"[{edge_type}] {target_doc}. "
                f"Exact Text: {text}"
            )
        else:
            linearized_string = f"Context: Clause {node_id}. Exact Text: {text}"

        return linearized_string

    def rerank(
        self, query: str, context_nodes: List[Dict], top_k: int = 2
    ) -> List[Dict]:
        """Scores the linearized paths against the user query using the Cross-Encoder."""
        print(
            f"\n[Reranker] Linearizing and scoring {len(context_nodes)} expanded nodes..."
        )
        if not context_nodes:
            return []

        safe_nodes = self._prune_dead_clauses(context_nodes)
        if not safe_nodes:
            return []

        linearized_docs = [self._linearize_context(n) for n in context_nodes]

        # FIX 2: SentenceTransformers requires pairs of [query, document]
        pairs = [[query, doc] for doc in linearized_docs]

        try:
            # FIX 3: Use .predict() instead of .rerank()
            scores = self.encoder.predict(pairs)

            # Map scores back to original context nodes
            for i, node in enumerate(context_nodes):
                node["rerank_score"] = round(float(scores[i]), 4)
                node["linearized_text"] = linearized_docs[i]

            # Sort descending by score
            safe_nodes.sort(key=lambda x: x["rerank_score"], reverse=True)

            # Keep top K
            ranked_nodes = safe_nodes[:top_k]

            print(f"  ↳ Reranking complete. Kept top {len(ranked_nodes)} nodes.")
            return ranked_nodes

        except Exception as e:
            print(f"⚠️ WARNING: Cross-Encoder failed. Reason: {e}")
            return safe_nodes


# --- EXECUTION FOR TESTING NEGATION FAILURE MODES ---
if __name__ == "__main__":
    mock_expanded_graph = [
        {
            "node_id": "Section 4(a)",
            "text": "The Vendor shall be liable for any API downtime penalty up to a maximum of $100,000.",
            "source_document": "Master SLA",
            "target_document": "N/A",
            "execution_date": "Jan 2024",
            "edge_type": "DEPENDS_ON",
        },
        {
            "node_id": "Addendum 1, Section 2",
            "text": "Notwithstanding the foregoing, the maximum downtime penalty shall not exceed $25,000.",
            "source_document": "SLA Amendment",
            "target_document": "Master SLA, Section 4(a)",
            "execution_date": "Oct 2025",
            "edge_type": "SUPERSEDES",
        },
    ]

    reranker = LegalCrossEncoder()
    test_query = "What is the maximum penalty for API downtime?"

    print(f"\nQuery: '{test_query}'")
    results = reranker.rerank(test_query, mock_expanded_graph)

    print("\n--- Final Cross-Encoder Ranking ---")
    for rank, r in enumerate(results):
        print(f"\nRank {rank + 1} (Score: {r['rerank_score']})")
        print(f"Linearized Input: {r['linearized_text']}")
