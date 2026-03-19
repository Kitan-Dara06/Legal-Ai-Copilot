import logging
import os

import sentry_sdk
from celery import Celery
from dotenv import load_dotenv
from sentry_sdk.integrations.celery import CeleryIntegration

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

# 1. Broker: RabbitMQ (task queue). Result backend: Redis (results only).
#    Using the same Redis for both broker and results does not scale well.
host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = os.getenv("UPSTASH_PASSWORD")

rabbitmq_url = os.getenv("RABBITMQ_URL")
if not rabbitmq_url:
    # Build from components (e.g. CloudAMQP, or local RabbitMQ)
    rq_host = os.getenv("RABBITMQ_HOST", "localhost")
    rq_port = os.getenv("RABBITMQ_PORT", "5672")
    rq_user = os.getenv("RABBITMQ_USER", "guest")
    rq_password = os.getenv("RABBITMQ_PASSWORD", "guest")
    rq_vhost = os.getenv("RABBITMQ_VHOST", "/")
    from urllib.parse import quote_plus

    rabbitmq_url = f"amqp://{rq_user}:{quote_plus(rq_password)}@{rq_host}:{rq_port}/{quote_plus(rq_vhost)}"

BROKER_URL = rabbitmq_url
REDIS_URL = f"rediss://default:{password}@{host}:{port}/0?ssl_cert_reqs=required"
broker_label = "RabbitMQ"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Create the Celery App
# ─────────────────────────────────────────────────────────────────────────────
celery_app = Celery(
    "legal_rag_worker",
    broker=BROKER_URL,
    backend=REDIS_URL,
    include=["app.tasks"],  # Where our task functions live
)

# Debug: log broker and backend (mask passwords)
logger = logging.getLogger(__name__)
safe_broker = BROKER_URL.split("@")[-1] if "@" in BROKER_URL else BROKER_URL
safe_redis = REDIS_URL.split("@")[-1] if "@" in REDIS_URL else "localhost"
logger.info("Celery Broker: %s (%s)", broker_label, safe_broker)
logger.info("Celery Result Backend: Redis (%s)", safe_redis)

# Explicit SSL configuration for Upstash/rediss result backend
if REDIS_URL.startswith("rediss://"):
    celery_app.conf.update(redis_backend_use_ssl={"ssl_cert_reqs": "required"})


# ─────────────────────────────────────────────────────────────────────────────
# 2. Configuration
# ─────────────────────────────────────────────────────────────────────────────
celery_app.conf.update(
    # Serialize tasks as JSON (human-readable, safe)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Don't store task results in Redis — status is in Postgres; avoids "Connection closed by server" when Redis drops idle connections (e.g. Upstash).
    task_ignore_result=True,
    # Broker robustness (CloudAMQP / RabbitMQ).
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=30,
    broker_heartbeat=30,
    broker_transport_options={
        # Kombu/amqp socket settings to reduce transient "read operation timed out"
        "connect_timeout": 30,
        "socket_timeout": 30,
    },
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # ── Priority Queues ──────────────────────────────────────────────────────
    # Two queues: "default" for clean PDFs, "ocr" for scanned/image PDFs.
    # This prevents a slow 50-page fax from blocking a fast digital contract.
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "ocr": {"exchange": "ocr", "routing_key": "ocr"},
    },
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    # Route specific tasks to specific queues
    task_routes={
        "app.tasks.process_digital_pdf": {"queue": "default"},
        "app.tasks.process_scanned_pdf": {"queue": "ocr"},
    },
    # Retry failed tasks up to 3 times with a 60-second delay
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # ── Periodic Check (Beat) ────────────────────────────────────────────────
    beat_schedule={
        "qdrant-heartbeat-every-12-hours": {
            "task": "app.tasks.qdrant_heartbeat",
            "schedule": 43200.0,  # 12 hours in seconds
        },
        "sweep-stuck-pending-every-5-minutes": {
            "task": "app.tasks.sweep_failed_tasks",
            "schedule": 300.0,  # every 5 minutes
        },
    },
)
