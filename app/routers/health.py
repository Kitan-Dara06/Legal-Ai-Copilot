# app/routers/health.py
#
# Lightweight health-check endpoints for deployment platform probing.
#
# GET /health  — check all dependencies; always 200 (informational)
# GET /ready   — same checks; 503 if any dependency is down

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["Health"])


@router.get("/live")
async def live():
    """
    Liveness probe.
    Keeps container health checks lightweight and independent of downstream services.
    """
    return {"status": "alive"}


async def _check_postgres() -> dict:
    try:
        from sqlalchemy import text

        from app.database import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def _check_redis() -> dict:
    try:
        from app.redis_client import get_redis_client

        r = get_redis_client()
        await r.ping()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def _check_qdrant() -> dict:
    try:
        import asyncio
        from app.services.store import get_global_qdrant

        client = get_global_qdrant()
        await asyncio.get_event_loop().run_in_executor(None, client.get_collections)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def _check_neo4j() -> dict:
    try:
        import asyncio
        from app.services.ingestion.graph_extractor import get_neo4j_driver
        driver = get_neo4j_driver()
        if not driver:
            return {"ok": False, "error": "Neo4j driver failed to initialize."}
        
        # Verify connectivity
        def _verify():
            driver.verify_connectivity()
            
        await asyncio.get_event_loop().run_in_executor(None, _verify)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def _check_rabbitmq() -> dict:
    try:
        from app.worker import celery_app
        # Attempt to inspect the broker connection
        # This is a bit heavy, but verifies connectivity
        celery_app.connection().ensure_connection(max_retries=1)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.get("/health")
async def health():
    """
    Informational health check. Always returns 200.
    """
    t0 = time.perf_counter()

    pg = await _check_postgres()
    rd = await _check_redis()
    qd = await _check_qdrant()
    nj = await _check_neo4j()
    rmq = await _check_rabbitmq()

    all_ok = all(c["ok"] for c in [pg, rd, qd, nj, rmq])

    return {
        "status": "ok" if all_ok else "degraded",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "checks": {
            "postgres": pg,
            "redis": rd,
            "qdrant": qd,
            "neo4j": nj,
            "rabbitmq": rmq,
        },
    }


@router.get("/ready")
async def ready():
    """
    Readiness probe. Returns 503 if any dependency is unhealthy.
    """
    pg = await _check_postgres()
    rd = await _check_redis()
    qd = await _check_qdrant()
    nj = await _check_neo4j()
    rmq = await _check_rabbitmq()

    checks = {
        "postgres": pg,
        "redis": rd,
        "qdrant": qd,
        "neo4j": nj,
        "rabbitmq": rmq,
    }
    all_ok = all(c["ok"] for c in checks.values())

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )
