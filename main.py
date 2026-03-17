import glob
import os
import time

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.database import Base, engine
from app.dependencies import get_org_id_for_rate_limit
from app.logging_config import configure_logging
from app.routers import agent_query, auth, health, injest, query, session

configure_logging()

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level="INFO", event_level="ERROR"),
        ],
        send_default_pii=False,
    )

limiter = Limiter(key_func=get_org_id_for_rate_limit)

# NOTE (proxy headers):
# This app is intended to run behind Nginx (see deploy/nginx.conf).
# Uvicorn should be started with proxy headers enabled so the app sees the real client IP/scheme.
# In Docker Compose, update the api command to include:
#   uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*
app = FastAPI(title="Legal RAG API", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(injest.router)
app.include_router(query.router)
app.include_router(agent_query.router)
app.include_router(session.router)
app.include_router(health.router)
app.include_router(auth.router)


@app.on_event("startup")
async def startup():
    """
    Startup behavior (production-safe):

    1) Database schema:
       - Prefer Alembic migrations in production.
       - Optionally allow create_all in dev/local only.
       - Optionally run Alembic upgrade head automatically if configured.

    2) Sweep orphaned temp files in app/uploads/ older than 10 minutes.
    """
    env = os.getenv("ENV", "development").lower()
    is_dev = env in {"dev", "development", "local"}

    # ── DB init / migrations ────────────────────────────────────────────────
    # Controls:
    # - DB_AUTO_CREATE_ALL=true      -> run SQLAlchemy create_all (dev only recommended)
    # - DB_RUN_MIGRATIONS=true       -> run `alembic upgrade head` on startup (prod option)
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
        # No-op by default in production: migrations should be run explicitly.
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
