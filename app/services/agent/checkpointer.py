"""
LangGraph Async Postgres Checkpointer
======================================
Uses `langgraph-checkpoint-postgres` to persist the graph state in bytea columns
via Postgres TOAST compression.

Key decisions:
  - Pool is lazy-initialized so DATABASE_URL is loaded from .env before connect.
  - setup() is called with SET statement_timeout = 0 to avoid Supabase's default
    timeout killing the DDL migrations (CREATE TABLE IF NOT EXISTS).
  - We use the standard postgresql:// DSN (no +asyncpg prefix).
"""
import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()
logger = logging.getLogger(__name__)


def _get_dsn() -> str:
    """Resolve DSN, stripping SQLAlchemy driver prefixes."""
    url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL", "")
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
            break
    return url


_pool: AsyncConnectionPool | None = None
# Checkpoint tables are pre-created via `scripts/setup_checkpointer.py`.
# Set to True to skip runtime DDL (avoids statement_timeout on Supabase).
_tables_created = True


async def _ensure_pool() -> AsyncConnectionPool:
    """Lazy-init singleton connection pool."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=_get_dsn(),
            max_size=20,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
    try:
        if not _pool._opened:
            await _pool.open()
    except AttributeError:
        await _pool.open()
    return _pool


async def _setup_tables(conn) -> None:
    """
    Run LangGraph checkpoint table migrations with statement_timeout disabled.
    Supabase sets a default statement_timeout that kills DDL; we reset it first.
    """
    global _tables_created
    if _tables_created:
        return
    try:
        # Disable statement timeout for DDL setup only
        await conn.execute("SET statement_timeout = 0")
        checkpointer = AsyncPostgresSaver(conn)
        await checkpointer.setup()
        _tables_created = True
        logger.info("LangGraph checkpoint tables ready.")
    except Exception as e:
        logger.warning("Checkpoint table setup failed (may already exist): %s", e)
        _tables_created = True  # assume tables exist, proceed


@asynccontextmanager
async def get_checkpointer():
    """
    Yields a configured AsyncPostgresSaver ready for graph.compile().

    Usage:
        async with get_checkpointer() as checkpointer:
            app = graph.compile(checkpointer=checkpointer, ...)
    """
    pool = await _ensure_pool()
    async with pool.connection() as conn:
        await _setup_tables(conn)
        yield AsyncPostgresSaver(conn)
