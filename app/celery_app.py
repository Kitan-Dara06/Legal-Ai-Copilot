# app/celery_app.py
#
# Lex SRS: Lightweight Celery application singleton.
#
# This module creates the Celery app with broker and backend URLs derived
# from environment variables. It does NOT:
#   - Import task modules (no `include`)
#   - Wire signal handlers
#   - Connect to any service
#
# Both the web server (for send_task) and the worker process import from here
# without triggering heavy dependency chains.

import logging
import os
from urllib.parse import quote_plus

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ── Broker (RabbitMQ) ──────────────────────────────────────────────────────
def _build_rabbitmq_url() -> str:
    """Build primary RabbitMQ broker URL from env vars."""
    rq_host = os.getenv("RABBITMQ_HOST", "localhost")
    rq_port = os.getenv("RABBITMQ_PORT", "5672")
    rq_user = os.getenv("RABBITMQ_USER", "guest")
    rq_password = os.getenv("RABBITMQ_PASSWORD", "guest")
    rq_vhost = os.getenv("RABBITMQ_VHOST", "/")
    return f"amqp://{rq_user}:{quote_plus(rq_password)}@{rq_host}:{rq_port}/{quote_plus(rq_vhost)}"


rabbitmq_url = os.getenv("RABBITMQ_URL")
if not rabbitmq_url:
    rabbitmq_url = _build_rabbitmq_url()

# Backup broker for failover
rabbitmq_url_two = os.getenv("RABBITMQ_URL_TWO")
if rabbitmq_url_two:
    # Kombu supports semicolon-separated failover URLs
    BROKER_URL = f"{rabbitmq_url};{rabbitmq_url_two}"
    broker_failover = True
    logger.info("RabbitMQ failover configured with backup broker.")
else:
    BROKER_URL = rabbitmq_url
    broker_failover = False

# ── Result Backend (Redis) ─────────────────────────────────────────────────
host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = os.getenv("UPSTASH_PASSWORD")

if not host or host == "localhost":
    REDIS_URL = (
        f"redis://{os.getenv('REDIS_HOST', 'localhost')}:"
        f"{os.getenv('REDIS_PORT', '6379')}/0"
    )
    broker_label = "RabbitMQ + Local Redis"
else:
    REDIS_URL = f"rediss://default:{password}@{host}:{port}/0?ssl_cert_reqs=required"
    broker_label = "RabbitMQ + Upstash"

# ── Create Celery App ──────────────────────────────────────────────────────
celery_app = Celery(
    "legal_rag_worker",
    broker=BROKER_URL,
    backend=REDIS_URL,
)

# Base serialisation — safe for string-based task dispatch
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_ignore_result=False,
)

# Explicit SSL configuration for rediss result backend
if REDIS_URL.startswith("rediss://"):
    celery_app.conf.update(redis_backend_use_ssl={"ssl_cert_reqs": "required"})

# Log broker and backend (mask passwords)
safe_broker = (
    ";".join(u.split("@")[-1] for u in BROKER_URL.split(";"))
    if "@" in BROKER_URL
    else BROKER_URL
)
safe_redis = REDIS_URL.split("@")[-1] if "@" in REDIS_URL else "localhost"
logger.info("Celery Broker: %s (%s)", broker_label, safe_broker)
logger.info("Celery Result Backend: Redis (%s)", safe_redis)
