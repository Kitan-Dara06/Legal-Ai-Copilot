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

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()
logger = logging.getLogger(__name__)


_pool: AsyncConnectionPool | None = None
_pool_loop_id = None
# Checkpoint tables are created on first use via _setup_tables().
_tables_created = False


def _reset_checkpointer_pool():
    """Reset pool after Celery fork or event loop change."""
    global _pool, _pool_loop_id
    _pool = None
    _pool_loop_id = None


try:
    os.register_at_fork(after_in_child=_reset_checkpointer_pool)
except AttributeError:
    pass


def _get_dsn() -> str:
    """Resolve DSN, stripping SQLAlchemy driver prefixes."""
    url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL", "")
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    return url


async def _ensure_pool() -> AsyncConnectionPool:
    """Lazy-init singleton connection pool with event loop detection."""
    global _pool, _pool_loop_id
    try:
        current_loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        current_loop_id = None
    if _pool is not None and _pool_loop_id != current_loop_id:
        _pool = None
        _pool_loop_id = None
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=_get_dsn(),
            max_size=20,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
        _pool_loop_id = current_loop_id
    if _pool is not None:
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
