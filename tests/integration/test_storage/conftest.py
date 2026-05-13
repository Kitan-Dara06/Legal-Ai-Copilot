import uuid

import pytest


@pytest.fixture
def test_blob_name():
    return f"unittest/{uuid.uuid4().hex}.pdf"


@pytest.fixture
def test_content():
    return b"%PDF-1.4 mock document content for integration test"
