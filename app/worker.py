# app/worker.py
#
# Lex SRS: Celery worker configuration.
# The celery_app itself lives in app.celery_app so that both the web server
# (via send_task) and the worker process can import it without pulling in
# task modules or signal handlers at import time.

import logging
import os

import sentry_sdk
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init
from dotenv import load_dotenv
from sentry_sdk.integrations.celery import CeleryIntegration

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
        send_default_pii=True,
    )

# ── Additional worker-only configuration ──────────────────────────────────

celery_app.conf.update(
    # Broker robustness (CloudAMQP / RabbitMQ).
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=30,
    broker_heartbeat=30,
    broker_transport_options={
        "connect_timeout": 30,
        "socket_timeout": 30,
    },
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Queues
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "ocr": {"exchange": "ocr", "routing_key": "ocr"},
        "deadline": {
            "exchange": "deadline",
            "routing_key": "deadline",
            "queue_arguments": {"x- durable": True},
        },
    },
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    task_routes={
        "app.tasks.process_digital_pdf": {"queue": "default"},
        "app.tasks.process_scanned_pdf": {"queue": "ocr"},
        "app.tasks.cleanup_stale_data": {"queue": "default"},
        "app.tasks.deadline_scanner": {"queue": "deadline"},
        "app.tasks.resolve_defined_term_conflicts": {"queue": "default"},
        "app.tasks.resolve_deadline_conflicts": {"queue": "deadline"},
    },
    # Beat Schedule (Deadline Scanner every 15 minutes)
    beat_schedule={
        "deadline-scanner-every-15-min": {
            "task": "app.tasks.deadline_scanner",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "deadline"},
        },
    },
    # Retry
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


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
