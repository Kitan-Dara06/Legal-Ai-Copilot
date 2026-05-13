#!/usr/bin/env python3
"""
Standalone hybrid retrieval test.
Usage:
  python test_retrieval.py "What happens if the company terminates the executive Without Cause?"
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(".env")

# Patch env to read from .env
os.environ.setdefault("FALKORDB_HOST", os.getenv("FALKORDB_HOST", "localhost"))
os.environ.setdefault("FALKORDB_PORT", os.getenv("FALKORDB_PORT", "6379"))
os.environ.setdefault("FALKORDB_PASSWORD", os.getenv("FALKORDB_PASSWORD", ""))
os.environ.setdefault("FALKORDB_SSL", os.getenv("FALKORDB_SSL", "false"))
os.environ.setdefault("FALKORDB_GRAPH", os.getenv("FALKORDB_GRAPH", "legal_rag"))


async def main():
    query = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else "What happens if the company terminates the executive Without Cause?"
    )
    org_id = os.getenv("TEST_ORG_ID", "")
    workspace_id = os.getenv("TEST_WORKSPACE_ID", "")

    if not org_id:
        print("ERROR: Set TEST_ORG_ID in .env or as env var")
        sys.exit(1)

    print(f"Query: {query}")
    print(f"Org ID: {org_id}")
    if workspace_id:
        print(f"Workspace ID: {workspace_id}")
    print("=" * 80)

    from app.services.embedder import get_embedding
    from app.services.store import search_hybrid

    embeddings = get_embedding([query])
    if not embeddings:
        print("ERROR: Embedding failed!")
        sys.exit(1)

    q_vector = embeddings[0]
    print(f"Query vector dims: {len(q_vector)}")
    print()

    results = search_hybrid(
        query_text=query,
        query_vector=q_vector,
        top_k=5,
        org_id=org_id,
        workspace_id=workspace_id if workspace_id else None,
    )

    if isinstance(results, dict):
        results = results.get("results", results)
    if not isinstance(results, list):
        results = []

    print(f"Returned {len(results)} chunks\n")

    for i, r in enumerate(results, 1):
        text = r.get("text", "")
        score = r.get("score", 0)
        meta = r.get("metadata", {})
        source = meta.get("source", "?")
        page = meta.get("page", "?")
        clause = meta.get("clause_reference", "")

        print(f"--- Result #{i} (score={score:.4f}) ---")
        print(f"Source: {source}, Page: {page}")
        if clause:
            print(f"Clause: {clause}")
        print(f"Content preview: {text[:300]}...")
        print()

    # Also show what the LLM would receive
    print("=" * 80)
    print("LLM CONTEXT (what gets sent to the model):")
    print("=" * 80)
    for i, r in enumerate(results, 1):
        text = r.get("text", "")
        source = r.get("metadata", {}).get("source", "Unknown")
        page = r.get("metadata", {}).get("page", "?")
        print(f"Source: {source} (Page {page})")
        print(text[:500])
        print()


if __name__ == "__main__":
    asyncio.run(main())
