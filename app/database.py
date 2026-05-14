# app/database.py

import os
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_database_url_async

load_dotenv()

_engine = None
_async_session_local = None


def _reset_engine():
    """Reset engine globals — called after Celery fork to force fresh pool creation."""
    global _engine, _async_session_local
    _engine = None
    _async_session_local = None


# Register fork handler so each Celery child process creates its own async engine
try:
    os.register_at_fork(after_in_child=_reset_engine)
except AttributeError:
    pass  # Windows doesn't support fork


def _get_engine():
    """Lazy engine creation — ensures each forked Celery worker gets its own pool."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_database_url_async(),
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_timeout=5,
            pool_pre_ping=True,
        )
    return _engine


def get_async_session() -> async_sessionmaker:
    """Returns a sessionmaker bound to a lazily-created async engine."""
    global _async_session_local
    if _async_session_local is None:
        _async_session_local = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_local


# Backward-compatible alias for existing imports
class _LazyAsyncSessionMaker:
    """Lazy proxy: first call creates engine + sessionmaker, subsequent calls reuse."""

    def __call__(self):
        return get_async_session()()

    def __await__(self):
        return self().__await__()

    async def __aenter__(self):
        self._session = get_async_session()()
        return await self._session.__aenter__()

    async def __aexit__(self, *args):
        return await self._session.__aexit__(*args)


AsyncSessionLocal = _LazyAsyncSessionMaker()


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_async_session()() as session:
        yield session
