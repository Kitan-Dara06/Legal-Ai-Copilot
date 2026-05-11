# app/redis_client.py
#
# Session structure in Redis:
#   Key:   "session:{uuid}"
#   Value: Hash map of { "file_id": "STATUS" }
#   TTL:   48 hours (auto-expires)
#
# LEX SRS COMPLIANT: No global connection pool at module level.
# Redis connections are created via:
#   - FastAPI lifespan → app.state.redis (for web server)
#   - create_redis_pool() directly  (for Celery workers)
#
# All session functions accept an explicit redis parameter.

import os
import uuid
from typing import Optional

import redis.asyncio as aioredis
from dotenv import load_dotenv

from app.config import redis_disable_tls_verify

load_dotenv()

host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = os.getenv("UPSTASH_PASSWORD")

# Sliding idle timeout: session expires 24h after the LAST time it was used.
# Every read (get_session) and write (add_file_to_session) resets this clock.
SESSION_TTL_SECONDS = 60 * 60 * 24
PROGRESS_TTL_SECONDS = 60 * 10


# ─────────────────────────────────────────────────────────────────────────────
# Redis Pool Factory
# ─────────────────────────────────────────────────────────────────────────────
def build_redis_url() -> str:
    """Construct the Redis URL from environment variables (no connection)."""
    base_url = f"rediss://default:{password}@{host}:{port}/0"
    if redis_disable_tls_verify():
        return base_url + "?ssl_cert_reqs=none"
    return base_url


def create_redis_pool() -> aioredis.Redis:
    """
    Create a new shared async Redis client backed by a connection pool.
    Called once during:
      - FastAPI lifespan startup (stored on app.state.redis)
      - Celery worker initialisation (per-process, after fork)
    """
    return aioredis.from_url(
        build_redis_url(),
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=3,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Session Helpers (all require an explicit redis parameter)
# ─────────────────────────────────────────────────────────────────────────────


async def create_session(
    file_ids: list[int], org_id: str, redis: aioredis.Redis
) -> str:
    """
    Creates a new session with the given file IDs.
    All files passed here are assumed to be READY (already processed).
    Returns the new session_id.
    """
    session_id = str(uuid.uuid4())
    session_key = f"session:{session_id}"

    session_data = {str(fid): "READY" for fid in file_ids}
    session_data["__org_id__"] = org_id

    await redis.hset(session_key, mapping=session_data)
    await redis.expire(session_key, SESSION_TTL_SECONDS)

    for fid in file_ids:
        await redis.sadd(f"file_sessions:{fid}", session_id)
        await redis.expire(f"file_sessions:{fid}", SESSION_TTL_SECONDS)

    return session_id


async def get_session(session_id: str, redis: aioredis.Redis) -> Optional[dict]:
    """
    Fetches the session data from Redis.
    Returns a dict of { file_id (int): status (str) }
    or None if the session doesn't exist / expired.
    """
    session_key = f"session:{session_id}"
    data = await redis.hgetall(session_key)

    if not data:
        return None
    await redis.expire(session_key, SESSION_TTL_SECONDS)

    org_id = data.pop("__org_id__", None)
    file_statuses = {int(k): v for k, v in data.items()}

    return {"org_id": org_id, "files": file_statuses}


async def add_file_to_session(
    session_id: str, file_id: int, redis: aioredis.Redis, status: str = "PROCESSING"
) -> bool:
    """
    Adds a new file to an existing session.
    Defaults to PROCESSING status (Celery hasn't finished yet).
    Returns False if the session doesn't exist.
    """
    session_key = f"session:{session_id}"

    if not await redis.exists(session_key):
        return False

    await redis.hset(session_key, str(file_id), status)

    await redis.sadd(f"file_sessions:{file_id}", session_id)
    await redis.expire(f"file_sessions:{file_id}", SESSION_TTL_SECONDS)

    await redis.expire(session_key, SESSION_TTL_SECONDS)
    return True


async def update_file_status_in_session(
    session_id: str, file_id: int, status: str, redis: aioredis.Redis
):
    """
    Called by Celery when a file finishes processing.
    Updates the file's status from PROCESSING -> READY (or FAILED).
    """
    session_key = f"session:{session_id}"
    await redis.hset(session_key, str(file_id), status)


async def remove_file_from_all_sessions(file_id: int, redis: aioredis.Redis):
    """
    Zombie File Fix: O(1) Deletion.
    Lookups the reverse index mapping file_id -> list of session_ids,
    deletes the file_id from those sessions, then clears the index.
    """
    sessions = await redis.smembers(f"file_sessions:{file_id}")
    for session_id in sessions:
        await redis.hdel(f"session:{session_id}", str(file_id))

    await redis.delete(f"file_sessions:{file_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Progress Tracking
# ─────────────────────────────────────────────────────────────────────────────


async def set_file_progress(file_id: int, percent: int, redis: aioredis.Redis):
    """
    Celery calls this every N chunks to report progress.
    percent: 0-100
    """
    await redis.set(f"progress:{file_id}", percent, ex=PROGRESS_TTL_SECONDS)


async def get_file_progress(file_id: int, redis: aioredis.Redis) -> Optional[int]:
    """Returns the current progress percentage (0-100) or None."""
    val = await redis.get(f"progress:{file_id}")
    return int(val) if val is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# LLM Slot Semaphore
# ─────────────────────────────────────────────────────────────────────────────

import time


async def acquire_llm_slot(
    org_id: str, redis: aioredis.Redis, max_slots: int = 5
) -> Optional[str]:
    """
    Distributed Token Bucket / Semaphore.
    Acquires a lease for LLM execution. Returns a lease_id if successful, None if full.
    """
    key = f"llm_slots:{org_id}"
    now = time.time()

    await redis.zremrangebyscore(key, 0, now - 120)
    count = await redis.zcard(key)
    if count >= max_slots:
        return None

    lease_id = uuid.uuid4().hex
    await redis.zadd(key, {lease_id: now})
    await redis.expire(key, 300)
    return lease_id


async def release_llm_slot(org_id: str, lease_id: str, redis: aioredis.Redis):
    """Releases an LLM slot lease."""
    key = f"llm_slots:{org_id}"
    await redis.zrem(key, lease_id)
