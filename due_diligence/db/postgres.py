"""
PostgreSQL Engine — legaltech schema
=====================================
Sync SQLAlchemy engine using DATABASE_URL from the environment.
All legaltech tables live in the 'legaltech' PostgreSQL schema
so they don't conflict with legal_rag's tables.
"""

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def init_schema():
    """Create the 'legaltech' schema and all tables if they don't exist."""
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS legaltech"))
    from due_diligence.db.pg_models import DefinedTerm, DealSession, AuditLog  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("✅ legaltech schema and tables ready.")


def get_session():
    """Context-manager-style session factory."""
    db = SessionLocal()
    try:
        yield db
    finally:
        due_diligence.db.close()
