"""Celery worker test configuration.

Uses task_always_eager=True to execute tasks synchronously without RabbitMQ.
"""

from unittest.mock import patch

import pytest
from celery import Celery

# Force Celery to run tasks synchronously for testing
TEST_CELERY_APP = Celery("test_worker")
TEST_CELERY_APP.conf.update(
    task_always_eager=True,
    task_eager_propagates=True,
    task_store_eager_result=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)


@pytest.fixture(autouse=True, scope="session")
def eager_celery():
    """Set task_always_eager=True on the real celery_app so tasks run synchronously."""
    from app.celery_app import celery_app as real_app

    real_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=True,
    )
    yield


@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock all external API calls so tests don't hit real services."""
    patches = [
        patch("app.services.object_storage.download_file"),
        patch("app.services.object_storage.upload_bytes"),
        patch("app.services.object_storage.object_exists", return_value=True),
        patch("app.services.object_storage.delete_file"),
        patch("groq.Groq"),
        patch("openai.OpenAI"),
        patch("voyageai.Client"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@pytest.fixture
def mock_db_session():
    """Provide a mock async DB session for task testing."""
    from unittest.mock import AsyncMock

    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def sample_doc_id():
    import uuid

    return uuid.uuid4()


@pytest.fixture
def sample_workspace_id():
    import uuid

    return uuid.uuid4()


@pytest.fixture
def sample_org_id():
    import uuid

    return uuid.uuid4()
