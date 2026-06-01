# app/beat.py
#
# Minimal Celery Beat entry point.
#
# Beat is a pure scheduler — it pushes task names onto the RabbitMQ queue
# on a cron schedule. It NEVER executes tasks. It does NOT need:
#   - app.tasks (fastembed, SPLADE, torch, voyage = hundreds of MB)
#   - The persistent async event loop
#   - DB connection warmup
#   - Hot-start signal handlers
#
# Memory footprint: ~80–100MB (vs ~1.5GB for app.worker)
#
# Usage:  celery -A app.beat beat --scheduler celery.beat:PersistentScheduler

import logging
import os

from celery.schedules import crontab
from dotenv import load_dotenv

from app.celery_app import celery_app  # broker + backend config only, no tasks
from app.logging_config import configure_logging

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

# ── Beat schedule ─────────────────────────────────────────────────────────────
# Tasks are referenced by name (string) only — no import of the actual function.
# The worker processes have the real implementations loaded.

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "deadline-scanner-every-15-min": {
            "task": "app.tasks.deadline_scanner",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "deadline"},
        },
        "stale-task-sweeper-every-5-min": {
            "task": "app.tasks.stale_task_sweeper",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "default"},
        },
    },
)

logger.info("Celery Beat scheduler initialized (lightweight entry point).")
