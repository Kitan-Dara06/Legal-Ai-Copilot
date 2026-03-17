import os

from dotenv import load_dotenv

load_dotenv()


def is_dev_environment() -> bool:
    """Simple helper to detect a non-production environment."""
    return os.getenv("ENV", "development").lower() in {"dev", "development", "local"}


def redis_disable_tls_verify() -> bool:
    """
    Central toggle for disabling Redis TLS verification.
    Should only be true in dev/local; default is secure (False).
    """
    return os.getenv("REDIS_DISABLE_TLS_VERIFY", "false").lower() == "true"


def get_database_url_async() -> str:
    """
    Returns the async SQLAlchemy database URL.
    Uses a sane local default for development if DATABASE_URL is missing.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url and is_dev_environment():
        db_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/legal_rag"
    if not db_url:
        raise RuntimeError("DATABASE_URL must be set in non-development environments.")
    if "postgresql://" in db_url and "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


def get_database_url_sync() -> str:
    """
    Returns the synchronous psycopg2-style database URL for Celery workers.
    """
    db_url = os.getenv("DATABASE_URL_SYNC")
    if not db_url and is_dev_environment():
        db_url = "postgresql://postgres:postgres@localhost:5432/legal_rag"
    if not db_url:
        raise RuntimeError("DATABASE_URL_SYNC must be set in non-development environments.")
    return db_url

