# app/redis_client.py

# Session structure in Redis:
#   Key:   "session:{uuid}"
#   Value: Hash map of { "file_id": "STATUS" }
#   TTL:   48 hours (auto-expires)

import json
import os
import uuid
from typing import Optional

import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = os.getenv("UPSTASH_PASSWORD")

# Sliding idle timeout: session expires 24h after the LAST time it was used.
# Every read (get_session) and write (add_file_to_session) resets this clock.
SESSION_TTL_SECONDS = 60 * 60 * 24   
PROGRESS_TTL_SECONDS = 60 * 10       


# ─────────────────────────────────────────────────────────────────────────────
# Global Redis Connection Pool
# ─────────────────────────────────────────────────────────────────────────────
REDIS_URL = f"rediss://default:{password}@{host}:{port}/0?ssl_cert_reqs=none"
_redis_client = aioredis.from_url(
    REDIS_URL,
    decode_responses=True,
    health_check_interval=30
)

def get_redis_client() -> aioredis.Redis:
    """Returns an async Redis client backed by the global connection pool."""
    return _redis_client


async def create_session(
    file_ids: list[int],
    org_id: str,
    redis: aioredis.Redis
) -> str:
    """
    Creates a new session with the given file IDs.
    All files passed here are assumed to be READY (already processed).
    Returns the new session_id.
    """
    session_id = str(uuid.uuid4())
    session_key = f"session:{session_id}"

    # Build the hash map: { "file_id": "READY" }
    # We also store org_id so we can verify ownership on queries.
    session_data = {str(fid): "READY" for fid in file_ids}
    session_data["__org_id__"] = org_id  

    await redis.hset(session_key, mapping=session_data)
    await redis.expire(session_key, SESSION_TTL_SECONDS)

    # Add to reverse index: file_sessions:{file_id} -> set(session_id)
    for fid in file_ids:
        await redis.sadd(f"file_sessions:{fid}", session_id)
        await redis.expire(f"file_sessions:{fid}", SESSION_TTL_SECONDS)

    return session_id


async def get_session(
    session_id: str,
    redis: aioredis.Redis
) -> Optional[dict]:
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
    session_id: str,
    file_id: int,
    redis: aioredis.Redis,
    status: str = "PROCESSING"
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
    
    # Add to reverse index
    await redis.sadd(f"file_sessions:{file_id}", session_id)
    await redis.expire(f"file_sessions:{file_id}", SESSION_TTL_SECONDS)
    
    # Refresh the TTL so the session doesn't expire mid-work
    await redis.expire(session_key, SESSION_TTL_SECONDS)
    return True


async def update_file_status_in_session(
    session_id: str,
    file_id: int,
    status: str,
    redis: aioredis.Redis
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
# 3. Progress Tracking
#    Celery updates this as it processes chunks.
#    The query endpoint reads this to show "45% done" messages.
# ─────────────────────────────────────────────────────────────────────────────

async def set_file_progress(file_id: int, percent: int, redis: aioredis.Redis):
    """
    Celery calls this every N chunks to report progress.
    percent: 0-100
    """
    await redis.set(f"progress:{file_id}", percent, ex=PROGRESS_TTL_SECONDS)


async def get_file_progress(file_id: int, redis: aioredis.Redis) -> int:
    """
    Returns the current processing progress (0-100) for a file.
    Returns 0 if no progress data found.
    """
    val = await redis.get(f"progress:{file_id}")
    return int(val) if val is not None else 0
