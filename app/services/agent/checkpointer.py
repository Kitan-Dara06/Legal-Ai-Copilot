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

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()
logger = logging.getLogger(__name__)


_pool: AsyncConnectionPool | None = None
# Checkpoint tables are created on first use via _setup_tables().
_tables_created = False


def _get_dsn() -> str:
    """Resolve DSN, stripping SQLAlchemy driver prefixes."""
    url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL", "")
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    return url


async def _ensure_pool() -> AsyncConnectionPool:
    """Lazy-init singleton connection pool. Logs creation to MongoDB audit."""
    global _pool
    if _pool is None or (hasattr(_pool, "closed") and _pool.closed):
        _pool = AsyncConnectionPool(
            conninfo=_get_dsn(),
            min_size=1,
            max_size=5,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "connect_timeout": 10,  # each individual TCP connection attempt: 10s max
            },
            open=False,
            timeout=15,  # max wait to acquire a connection once pool is open
        )
        await _pool.open(wait=True, timeout=30)  # 30s hard cap on pool initialization
        logger.info("Checkpointer pool opened.")

        # MongoDB audit: log pool creation
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import DebugCategory, DebugTrace, LogLevel

            AuditLogger.debug(
                DebugTrace(
                    level=LogLevel.INFO,
                    category=DebugCategory.DB_QUERY,
                    message="Checkpointer pool created",
                    metadata={"min_size": 1, "max_size": 5},
                )
            )
        except Exception:
            pass

    return _pool


async def _setup_tables(conn) -> None:
    """
    Run LangGraph checkpoint table migrations with statement_timeout disabled.
    Logs success/failure to MongoDB audit.
    """
    global _tables_created
    if _tables_created:
        return
    try:
        await conn.execute("SET statement_timeout = 0")
        checkpointer = AsyncPostgresSaver(conn)
        await checkpointer.setup()
        _tables_created = True
        logger.info("LangGraph checkpoint tables ready.")

        # MongoDB audit: log table setup
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import AuditCategory, AuditEvent, LogLevel

            AuditLogger.audit(
                AuditEvent(
                    level=LogLevel.INFO,
                    category=AuditCategory.DB_QUERY,
                    action="checkpointer.tables_ready",
                    message="LangGraph checkpoint tables created/verified",
                )
            )
        except Exception:
            pass

    except Exception as e:
        logger.warning("Checkpoint table setup failed (may already exist): %s", e)
        _tables_created = True


@asynccontextmanager
async def get_checkpointer():
    """
    Yields a configured AsyncPostgresSaver ready for graph.compile().

    Logs pool init, table setup, and connection lifecycle to MongoDB audit.

    Usage:
        async with get_checkpointer() as checkpointer:
            app = graph.compile(checkpointer=checkpointer, ...)
    """
    pool = await _ensure_pool()
    async with pool.connection(timeout=30) as conn:  # 30s cap — fails fast if pool broken
        await _setup_tables(conn)

        # MongoDB audit: log connection acquisition
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import DebugCategory, DebugTrace

            AuditLogger.debug(
                DebugTrace(
                    category=DebugCategory.DB_QUERY,
                    message="Checkpointer connection acquired",
                    metadata={"pool_min": 1, "pool_max": 5},
                )
            )
        except Exception:
            pass

        yield AsyncPostgresSaver(conn)
