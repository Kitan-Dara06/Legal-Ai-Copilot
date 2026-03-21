import glob
import logging
import os
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
from app.routers import agent_query, auth, health, injest, invites, query, session

configure_logging()

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.getenv("ENV", "development"),
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.WARNING),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "1.0")),
        send_default_pii=True,  # Changed to True to capture users, but we handle it via middleware
    )

limiter = Limiter(key_func=get_org_id_for_rate_limit)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager (replaces deprecated on_event).

    Startup:
      1) Database schema: run Alembic or create_all in dev.
      2) Sweep orphaned temp files in app/uploads/ older than 10 minutes.
    """
    env = os.getenv("ENV", "development").lower()
    is_dev = env in {"dev", "development", "local"}

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
            print(f"⚠️  Alembic migration failed at startup: {e}")
            print("   The app will still start. DB errors will surface per-request.")

    elif auto_create_all and is_dev:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("✅ Database tables ready (create_all).")
        except Exception as e:
            print(f"⚠️  Could not connect to database at startup: {e}")
            print("   The app will still start. DB errors will surface per-request.")
    else:
        if not is_dev:
            print("ℹ️  Skipping DB create_all on startup (use Alembic migrations).")

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

    # Shutdown (nothing to clean up currently)


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

# NOTE (proxy headers):
# This app is intended to run behind Nginx (see deploy/nginx.conf).
# Uvicorn should be started with proxy headers enabled so the app sees the real client IP/scheme.
# In Docker Compose, update the api command to include:
#   uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*
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
)
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    cast(ExceptionHandler, _rate_limit_exceeded_handler),
)

# ── CORS Middleware ──────────────────────────────────────────────────────────
# Allow the Streamlit frontend and any future clients to make cross-origin requests.
_allowed_origins = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
if not _allowed_origins:
    # Reasonable defaults: production domain + localhost for dev
    _allowed_origins = [
        "https://legalrag.codes",
        "https://www.legalrag.codes",
        "https://legal-ai-copilot-xi.vercel.app",
        "http://localhost:8501",
        "http://localhost:3000",
    ]

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
