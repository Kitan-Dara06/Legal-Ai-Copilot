# tests/conftest.py
# Shared pytest fixtures for all tests
import pytest
import os
from dotenv import load_dotenv

# Load .env so service calls work in tests
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


@pytest.fixture(scope="session")
def api_base_url():
    return os.getenv("API_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def org_id():
    return "stream_ui_org"
