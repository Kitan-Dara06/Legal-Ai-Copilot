# app/services/audit/client.py
#
# MongoDB connection manager using Motor (async driver).
# Initialised once at app/worker startup, shared via module-level ref.

import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_audit_db = None
_debug_db = None

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_AUDIT_DB", "legal_rag_audit")


async def init_audit_db(mongo_uri: str | None = None, db_name: str | None = None):
    """Initialise the shared Motor client and ensure indexes exist."""
    global _client, _audit_db, _debug_db

    uri = mongo_uri or MONGO_URI
    name = db_name or DB_NAME

    if _client is not None:
        return  # already initialised

    _client = AsyncIOMotorClient(
        uri,
        maxPoolSize=10,
        minPoolSize=1,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    _audit_db = _client[name]

    # Verify connection
    try:
        await _client.admin.command("ping")
        logger.info("MongoDB connected: %s/%s", uri.split("@")[-1], name)
    except Exception as e:
        logger.warning("MongoDB connection failed (non-fatal): %s", e)
        _client = None
        return

    # ── Indexes: audit_events (90-day TTL) ─────────────────────────────────
    audit = _audit_db.audit_events
    await audit.create_index("timestamp", expireAfterSeconds=7776000)  # 90 days
    await audit.create_index([("workflow_id", 1), ("timestamp", -1)])
    await audit.create_index([("task_id", 1), ("timestamp", -1)])
    await audit.create_index([("correlation_id", 1)])
    await audit.create_index([("category", 1), ("timestamp", -1)])

    # ── Indexes: debug_traces (7-day TTL) ──────────────────────────────────
    debug = _audit_db.debug_traces
    await debug.create_index("timestamp", expireAfterSeconds=604800)  # 7 days
    await debug.create_index([("workflow_id", 1), ("timestamp", -1)])
    await debug.create_index([("node", 1), ("timestamp", -1)])
    await debug.create_index([("correlation_id", 1)])

    logger.info("MongoDB indexes ensured.")


def get_audit_db():
    """Return the audit DB handle (may be None if Mongo is down)."""
    return _audit_db


async def close_audit_db():
    """Close the Motor client gracefully on shutdown."""
    global _client, _audit_db, _debug_db
    if _client:
        _client.close()
        _client = None
        _audit_db = None
        _debug_db = None
        logger.info("MongoDB connection closed.")
