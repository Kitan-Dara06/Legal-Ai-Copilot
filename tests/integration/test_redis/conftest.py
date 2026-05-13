import pytest
import pytest_asyncio

from app.redis_client import create_redis_pool


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def redis():
    pool = create_redis_pool()
    yield pool
    await pool.aclose()
