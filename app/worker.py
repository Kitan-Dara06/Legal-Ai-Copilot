# app/worker.py
#
# Lex SRS: Celery worker configuration.
# The celery_app itself lives in app.celery_app so that both the web server
# (via send_task) and the worker process can import it without pulling in
# task modules or signal handlers at import time.

import asyncio
import logging
import os
import threading

import sentry_sdk

logger = logging.getLogger(__name__)
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, worker_process_shutdown
from dotenv import load_dotenv
from sentry_sdk.integrations.celery import CeleryIntegration

import app.tasks  # noqa: F401 — register task decorators
from app.celery_app import celery_app  # noqa: F401 — re-export for convenience
from app.logging_config import configure_logging

load_dotenv()
configure_logging()

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.getenv("ENV", "development"),
        integrations=[CeleryIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
    )

# ── Additional worker-only configuration ──────────────────────────────────

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=30,
    broker_heartbeat=10,
    broker_heartbeat_checkrate=2,
    broker_transport_options={
        "connect_timeout": 30,
        "socket_timeout": 30,
        "failover_strategy": "shuffle",
        "heartbeat": 10,
    },
    worker_cancel_long_running_tasks_on_connection_loss=True,
    timezone="UTC",
    enable_utc=True,
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "ocr": {"exchange": "ocr", "routing_key": "ocr"},
        "deadline": {
            "exchange": "deadline",
            "routing_key": "deadline",
        },
    },
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    task_routes={
        "app.tasks.process_digital_pdf": {"queue": "default"},
        "app.tasks.process_scanned_pdf": {"queue": "ocr"},
        "app.tasks.process_workflow": {"queue": "default"},
        "app.tasks.cleanup_stale_data": {"queue": "default"},
        "app.tasks.deadline_scanner": {"queue": "deadline"},
        "app.tasks.resolve_defined_term_conflicts": {"queue": "default"},
        "app.tasks.resolve_deadline_conflicts": {"queue": "deadline"},
    },
    beat_schedule={
        "deadline-scanner-every-15-min": {
            "task": "app.tasks.deadline_scanner",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "deadline"},
        },
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# ── Persistent event loop (initialized at import time) ────────────────────

_worker_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
_worker_loop_thread: threading.Thread | None = None


def _start_loop():
    """Start the persistent event loop in a background thread."""
    global _worker_loop_thread

    def run_loop():
        asyncio.set_event_loop(_worker_loop)
        _worker_loop.run_forever()

    _worker_loop_thread = threading.Thread(
        target=run_loop,
        daemon=True,
        name="celery-async-loop",
    )
    _worker_loop_thread.start()
    logger.info("Persistent async loop started: %s", id(_worker_loop))


_start_loop()


def get_worker_loop() -> asyncio.AbstractEventLoop:
    if not _worker_loop.is_running():
        raise RuntimeError("Worker loop is not running")
    return _worker_loop


async def _init_audit_for_worker():
    """Start the MongoDB audit consumer on a dedicated daemon thread."""
    try:
        from app.services.audit.logger import start_consumer

        start_consumer()
        logger.info("MongoDB audit consumer thread started.")
    except Exception as e:
        logger.warning("MongoDB audit init failed (non-fatal): %s", e)


# ── Warmup and teardown (run on the persistent loop) ──────────────────────


@worker_process_init.connect
def init_worker_process(**kwargs):
    """Warm up DB connections when worker process starts."""
    future = asyncio.run_coroutine_threadsafe(_warmup(), _worker_loop)
    try:
        future.result(timeout=120)  # 120s — cold Supabase SSL + checkpointer DDL can take 60-90s
        logger.info("Worker warmup complete (DB connections ready).")
    except Exception as e:
        # repr(e) shows type even when str(e) is blank (e.g. TimeoutError)
        logger.error("Worker warmup failed (non-fatal): %s", repr(e))

    # MongoDB audit init — start_consumer() fires its own daemon thread
    # with a dedicated event loop. This works synchronously (no asyncio needed
    # because the _worker_loop thread doesn't survive os.fork()).
    try:
        from app.services.audit.logger import start_consumer

        start_consumer()
        logger.info("MongoDB audit consumer started.")
    except Exception:
        pass  # non-fatal


@worker_process_shutdown.connect
def shutdown_worker_process(**kwargs):
    """Tear down connections when worker shuts down."""
    future = asyncio.run_coroutine_threadsafe(_teardown(), _worker_loop)
    try:
        future.result(timeout=10)
    except Exception:
        pass


async def _warmup():
    """Initialize DB engine and pre-warm the checkpointer pool + tables.

    Runs the full checkpointer setup (pool open + DDL migrations) so that
    the first task finds _tables_created=True and skips straight to ainvoke
    instead of blocking for minutes on a cold PostgreSQL connection.
    """
    import sqlalchemy as sa

    from app.database import _get_engine
    from app.services.agent.checkpointer import _ensure_pool, _setup_tables

    # 1. Pre-warm the SQLAlchemy engine (FastAPI / general DB)
    engine = _get_engine()
    async with engine.connect() as conn:
        await conn.execute(sa.text("SELECT 1"))
    logger.info("SQLAlchemy engine warmed.")

    # 2. Pre-warm the checkpointer pool AND run table migrations
    #    so _tables_created=True before the first task arrives.
    pool = await _ensure_pool()
    async with pool.connection(timeout=30) as conn:
        await _setup_tables(conn)
    logger.info("Checkpointer pool + tables ready.")


async def _teardown():
    """Close connections gracefully."""
    from app.database import _engine
    from app.services.agent.checkpointer import _pool

    if _pool is not None:
        await _pool.close()
    if _engine is not None:
        await _engine.dispose()


# ── Hot-start (existing) ──────────────────────────────────────────────────


@worker_process_init.connect
def _celery_hot_start(**_kwargs):
    """
    Hot-start heavy imports inside Celery worker processes.
    """
    hot_start = os.getenv("CELERY_HOT_START", "true").lower() == "true"
    include_gemini = (
        os.getenv("CELERY_HOT_START_INCLUDE_GEMINI", "false").lower() == "true"
    )
    strict = os.getenv("CELERY_HOT_START_STRICT", "true").lower() == "true"
    if not hot_start:
        return
    try:
        from app.tasks import warmup_heavy_dependencies

        warmup_heavy_dependencies(include_gemini=include_gemini)
        logger.info("✅ Celery hot-start complete (heavy deps warmed).")
    except Exception as e:
        logger.error("❌ Celery hot-start failed: %s", e)
        try:
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
        if strict:
            raise
