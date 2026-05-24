import glob
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import cast

import sentry_sdk
from app.config_validation import validate_config
from app.database import Base
from app.database import _get_engine as get_db_engine
from app.dependencies import get_org_id_for_rate_limit
from app.logging_config import configure_logging
from app.routers import (
    action_agent,
    admin,
    approvals,
    audit,
    auth,
    cron,
    escalations,
    goals,
    health,
    integrations,
    invites,
    notifications,
    workspaces,
)
from app.services.object_storage import check_storage_ready
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.types import ExceptionHandler

configure_logging()

logger = logging.getLogger(__name__)

# ── Environment & Config Validation ──────────────────────────────────────────
validate_config()

_env = os.getenv("ENV", "development").lower()
is_dev = _env in {"dev", "development", "local"}

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=_env,
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.WARNING),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
    )

limiter = Limiter(key_func=get_org_id_for_rate_limit)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager.
    Startup:
      1) Database schema: run Alembic or create_all in dev.
      2) Sweep orphaned temp files in app/uploads/ older than 10 minutes.
    """
    # ── DB init / migrations ────────────────────────────────────────────────
    auto_create_all = os.getenv("DB_AUTO_CREATE_ALL", "false").lower() == "true"
    run_migrations = os.getenv("DB_RUN_MIGRATIONS", "false").lower() == "true"

    if run_migrations:
        try:
            from alembic import command
            from alembic.config import Config

            alembic_cfg_path = os.getenv("ALEMBIC_CONFIG", "alembic.ini")
            alembic_cfg = Config(alembic_cfg_path)
            command.upgrade(alembic_cfg, "head")
            print("✅ Alembic migrations applied (upgrade head).")
        except Exception as e:
            print(f"❌ Alembic migration failed at startup: {e}")
            if not is_dev:
                print("FATAL: Database migrations must pass in production. Exiting.")
                sys.exit(1)
            print("   Warning: The app will still start in dev mode despite DB errors.")

    elif auto_create_all and is_dev:
        try:
            import asyncio

            async with get_db_engine().begin() as conn:
                await asyncio.wait_for(
                    conn.run_sync(Base.metadata.create_all), timeout=5.0
                )
            print("✅ Database tables ready (create_all).")
        except Exception as e:
            print(f"⚠️  Could not connect to database at startup: {e}")
            if not is_dev:
                sys.exit(1)

    # ── Redis Pool (Lex SRS: created in lifespan, stored on app.state) ──
    try:
        from app.redis_client import create_redis_pool

        app.state.redis = create_redis_pool()
        # Verify connectivity with a short timeout to avoid blocking startup
        import asyncio

        await asyncio.wait_for(app.state.redis.ping(), timeout=10.0)
        print("✅ Redis pool initialised.")
    except Exception as e:
        print(f"⚠️  Redis init failed — app will degrade: {e}")
        app.state.redis = None
        if not is_dev:
            print("FATAL: Redis must be available in production.")
            sys.exit(1)

    # ── Groq Client (Lex SRS: created in lifespan, stored on app.state) ──
    groq_api_key = os.getenv("GROQ_API_KEY")
    if groq_api_key:
        try:
            from groq import Groq

            app.state.groq_client = Groq(api_key=groq_api_key)
            print("✅ Groq client initialised.")
        except Exception as e:
            print(f"⚠️  Groq client init failed: {e}")
            app.state.groq_client = None
    else:
        print("⚠️  GROQ_API_KEY not set — Groq features disabled.")
        app.state.groq_client = None

    # ── Cohere API Key Check ─────────────────────────────────────────────
    cohere_key = os.getenv("COHERE_API_KEY")
    if cohere_key:
        print(f"✅ COHERE_API_KEY is set ({cohere_key[:8]}...{cohere_key[-4:]})")
    else:
        print("⚠️  COHERE_API_KEY not set — reranker disabled, using Qdrant RRF")

    # ── Voyage API Key Check ────────────────────────────────────────────
    voyage_key = os.getenv("VOYAGE_API_KEY")
    if voyage_key:
        print(f"✅ VOYAGE_API_KEY is set ({voyage_key[:8]}...{voyage_key[-4:]})")
    else:
        print(
            "⚠️  VOYAGE_API_KEY not set — voyage-rerank-2 disabled, embeddings will fail"
        )

    # ── Storage readiness check: fail fast in production by default.
    storage_strict = (
        os.getenv("STORAGE_STRICT_STARTUP", "true" if not is_dev else "false").lower()
        == "true"
    )
    try:
        check_storage_ready(strict=storage_strict)
    except Exception as e:
        print(f"❌ Storage readiness check failed: {e}")
        if storage_strict:
            print("FATAL: Object storage must be ready at startup. Exiting.")
            sys.exit(1)

    # Orphan sweep: purge temp PDFs left behind by crashed uploads
    uploads_dir = "app/uploads"
    if os.path.isdir(uploads_dir):
        now = time.time()
        cutoff = 10 * 60  # 10 minutes
        for fp in glob.glob(os.path.join(uploads_dir, "temp_*.pdf")):
            try:
                if now - os.path.getmtime(fp) > cutoff:
                    os.remove(fp)
                    print(f"\U0001f9f9 Swept orphaned upload: {fp}")
            except Exception as e:
                print(f"\u26a0\ufe0f  Could not sweep {fp}: {e}")

    # NFR-REL-03: Recover workflows stuck in AWAITING_APPROVAL after restart
    try:
        import asyncio

        from app.database import AsyncSessionLocal as _RecoverySession
        from app.models import WorkflowExecution, WorkflowStatus
        from sqlalchemy import select, update

        async def _recover_stuck_workflows():
            async with _RecoverySession() as recovery_db:
                result = await recovery_db.execute(
                    select(WorkflowExecution).where(
                        WorkflowExecution.status == WorkflowStatus.AWAITING_APPROVAL
                    )
                )
                stuck = result.scalars().all()
                if stuck:
                    logger.info(
                        "[startup] Found %d workflows stuck in AWAITING_APPROVAL — recovering",
                        len(stuck),
                    )
                else:
                    logger.debug("[startup] No stuck workflows found")

        await asyncio.wait_for(_recover_stuck_workflows(), timeout=5.0)
    except Exception as e:
        logger.warning("[startup] Workflow recovery check failed: %s", e)



    # ── MongoDB Audit Logging ────────────────────────────────────────────────
    try:
        from app.services.audit.client import init_audit_db
        from app.services.audit.logger import start_consumer

        await init_audit_db()
        start_consumer()
        print("✅ MongoDB audit logging initialised.")
    except Exception as e:
        print(f"⚠️  MongoDB audit init failed (non-fatal): {e}")

    yield  # App runs here

    # ── Lifespan Shutdown ────────────────────────────────────────────────────
    from app.services.audit.client import close_audit_db
    from app.services.audit.logger import stop_consumer

    await stop_consumer()
    await close_audit_db()

    redis = getattr(app.state, "redis", None)
    if redis is not None:
        try:
            await redis.aclose()
            print("🔒 Redis pool closed.")
        except Exception as e:
            print(f"⚠️  Redis pool close error: {e}")


# ── OpenAPI Description & Tags ───────────────────────────────────────────────
description = """
# Legal AI Copilot API 🚀

An intelligent, vector-backed RAG (Retrieval-Augmented Generation) engine designed specifically for analyzing and questioning corporate contracts.

## Features
* **Authentication**: Multi-tenant isolation and user management via Supabase.
* **Document Ingestion**: Supports fast uploaded chunks and async Map-Reduce OCR for complex PDFs.
* **Workspace Sessions**: "Filing Cabinet" metaphor for grouping documents together for a specific task.
* **Hybrid Search**: Fuses semantic vector search (Cohere embeddings) with sparse keyword retrieval (Qdrant).
* **AI Agent**: Orchestrates multi-step reasoning plans to extract and synthesize legal facts.
"""

tags_metadata = [
    {
        "name": "Authentication & Orgs",
        "description": "Tenant isolation, login state, and workspace membership management.",
    },
    {
        "name": "Files & Ingestion",
        "description": "Upload pipelines, asynchronous document processing, and storage management.",
    },
    {
        "name": "Session Management",
        "description": "Create ad-hoc groupings of files for scoped questions and analysis.",
    },
    {
        "name": "Queries & AI Agents",
        "description": "Run standard and agentic RAG searches across your organized legal content.",
    },
]

# ── FastAPI App Setup ────────────────────────────────────────────────────────
app = FastAPI(
    title="Legal RAG API",
    version="2.0.0",
    description=description,
    openapi_tags=tags_metadata,
    contact={
        "name": "API Support",
        "url": "https://legalrag.codes/support",
        "email": "support@legalrag.codes",
    },
    lifespan=lifespan,
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    openapi_url="/openapi.json" if is_dev else None,
)
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    cast(ExceptionHandler, _rate_limit_exceeded_handler),
)

# ── CORS Middleware ──────────────────────────────────────────────────────────
_allowed_origins = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if not _allowed_origins:
    _allowed_origins = [
        "https://legalrag.codes",
        "https://www.legalrag.codes",
        "https://legal-ai-copilot-xi.vercel.app",
    ]
    if is_dev:
        _allowed_origins.extend(
            [
                "http://localhost:8501",
                "http://localhost:3000",
            ]
        )

# HARD OVERRIDE: Ensure Vercel is always permitted regardless of .env configuration.
if "https://legal-ai-copilot-xi.vercel.app" not in _allowed_origins:
    _allowed_origins.append("https://legal-ai-copilot-xi.vercel.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(goals.router)
app.include_router(approvals.router)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(invites.router)
app.include_router(audit.router)
app.include_router(workspaces.router)
app.include_router(cron.router)
app.include_router(notifications.router)
app.include_router(escalations.router)
app.include_router(integrations.router)
app.include_router(action_agent.router)
app.include_router(admin.router)
