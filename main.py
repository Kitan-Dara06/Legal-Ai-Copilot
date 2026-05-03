import glob
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import cast

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.types import ExceptionHandler

from app.database import Base, engine
from app.dependencies import get_org_id_for_rate_limit
from app.logging_config import configure_logging
from app.routers import agent_query, auth, health, injest, invites, query, session, action_agent
from app.config_validation import validate_config
from app.services.object_storage import check_storage_ready

configure_logging()

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
        send_default_pii=True,
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
            from alembic.config import Config
            from alembic import command

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
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("✅ Database tables ready (create_all).")
        except Exception as e:
            print(f"⚠️  Could not connect to database at startup: {e}")
            if not is_dev:
                sys.exit(1)

    # Storage readiness check: fail fast in production by default.
    storage_strict = os.getenv("STORAGE_STRICT_STARTUP", "true" if not is_dev else "false").lower() == "true"
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
                    print(f"🧹 Swept orphaned upload: {fp}")
            except Exception as e:
                print(f"⚠️  Could not sweep {fp}: {e}")

    yield  # App runs here

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
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
if not _allowed_origins:
    _allowed_origins = [
        "https://legalrag.codes",
        "https://www.legalrag.codes",
        "https://legal-ai-copilot-xi.vercel.app",
    ]
    if is_dev:
        _allowed_origins.extend([
            "http://localhost:8501",
            "http://localhost:3000",
        ])

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

app.include_router(injest.router)
app.include_router(query.router)
app.include_router(agent_query.router)
app.include_router(session.router)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(invites.router)
app.include_router(action_agent.router)

from app.routers import cron
app.include_router(cron.router)

# ── Due Diligence (agentic pipeline) ────────────────────────────────────────
from due_diligence.api.routes import router as _due_diligence_router
app.include_router(
    _due_diligence_router,
    prefix="/api/v1/due-diligence",
    tags=["Due Diligence"],
)
