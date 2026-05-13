"""Shared fixtures for FastAPI endpoint tests.

Mocks are applied BEFORE creating the TestClient so the app's lifespan
(Redis, DB, etc.) never connects to real infrastructure.
"""

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Patch startup-blocking functions BEFORE importing the app ─────────────────
_patches = [
    patch("app.redis_client.create_redis_pool"),
    patch("app.services.object_storage.check_storage_ready", return_value=True),
    patch("app.services.object_storage._get_client"),
    patch("groq.Groq"),
    patch("openai.OpenAI"),
    patch("voyageai.Client"),
    patch("cohere.ClientV2"),
    patch("qdrant_client.QdrantClient"),
    patch("redis.asyncio.Redis"),
    patch("app.celery_app.celery_app"),
]
for _p in _patches:
    _p.start()

from main import app


@pytest.fixture(scope="session")
def client():
    """Provide a FastAPI TestClient — lifespan runs with mocks already active."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_jwt_token():
    return (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwiYXVkIjoiYXV0aGVudGljYXRlZCIsInJvbGUiOiJNRU1CRVIiLCJvcmdfaWQiOiJ0ZXN0LW9yZy0xMjMifQ."
        "test"
    )


@pytest.fixture
def auth_header(mock_jwt_token):
    return {"Authorization": f"Bearer {mock_jwt_token}"}


@pytest.fixture
def valid_workspace_id():
    return str(uuid.uuid4())


@pytest.fixture
def valid_goal_payload():
    return {
        "goal_text": "Summarize the confidentiality clauses in these contracts",
        "mode": "hybrid",
    }
