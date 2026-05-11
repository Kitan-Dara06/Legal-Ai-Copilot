"""
Pytest conftest for Qdrant integration tests.

Creates a dedicated test collection with 5 sample points before each test
session and tears it down afterwards.
"""

import os
import uuid

import pytest
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)

TEST_COLLECTION = "test_integration_collection"
DENSE_DIM = 4  # Small dimension for test vectors


@pytest.fixture(scope="session")
def qdrant_client():
    """Return a QdrantClient connected to the real Qdrant instance."""
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
    return client


@pytest.fixture(scope="session", autouse=True)
def test_collection(qdrant_client):
    """
    Create a test collection with 5 sample points, yield for tests, then
    delete the collection during teardown.
    """
    # --- Setup: delete any leftover from a previous interrupted run ---
    if qdrant_client.collection_exists(TEST_COLLECTION):
        qdrant_client.delete_collection(collection_name=TEST_COLLECTION)

    # Create the collection
    qdrant_client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config={
            "dense": VectorParams(size=DENSE_DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={"sparse": SparseVectorParams()},
    )

    # Upsert 5 sample points
    points = [
        PointStruct(
            id=1,
            vector={
                "dense": [0.1, 0.2, 0.3, 0.4],
                "sparse": SparseVector(indices=[0, 2], values=[0.5, 0.8]),
            },
            payload={
                "title": "Contract A",
                "org_id": "org_A",
                "text": "This is the first sample contract about confidentiality.",
                "page": 1,
            },
        ),
        PointStruct(
            id=2,
            vector={
                "dense": [0.2, 0.3, 0.4, 0.5],
                "sparse": SparseVector(indices=[1, 3], values=[0.6, 0.7]),
            },
            payload={
                "title": "Contract B",
                "org_id": "org_A",
                "text": "Second contract covering non-disclosure obligations.",
                "page": 2,
            },
        ),
        PointStruct(
            id=3,
            vector={
                "dense": [0.3, 0.4, 0.5, 0.6],
                "sparse": SparseVector(indices=[0, 1], values=[0.9, 0.3]),
            },
            payload={
                "title": "Contract C",
                "org_id": "org_B",
                "text": "Third contract about licensing and royalties.",
                "page": 1,
            },
        ),
        PointStruct(
            id=4,
            vector={
                "dense": [0.4, 0.5, 0.6, 0.7],
                "sparse": SparseVector(indices=[2, 3], values=[0.4, 0.5]),
            },
            payload={
                "title": "Contract D",
                "org_id": "org_B",
                "text": "Fourth contract regarding employment terms.",
                "page": 3,
            },
        ),
        PointStruct(
            id=5,
            vector={
                "dense": [0.5, 0.6, 0.7, 0.8],
                "sparse": SparseVector(indices=[0, 3], values=[0.7, 0.6]),
            },
            payload={
                "title": "Contract E",
                "org_id": "org_C",
                "text": "Fifth contract about data processing agreements.",
                "page": 2,
            },
        ),
    ]

    qdrant_client.upsert(collection_name=TEST_COLLECTION, points=points)

    # Optionally wait a moment for indexing (Qdrant is near-realtime)
    qdrant_client.count(collection_name=TEST_COLLECTION, exact=True)

    yield TEST_COLLECTION

    # --- Teardown ---
    if qdrant_client.collection_exists(TEST_COLLECTION):
        qdrant_client.delete_collection(collection_name=TEST_COLLECTION)
