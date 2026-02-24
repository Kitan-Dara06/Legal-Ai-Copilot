import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# 1. Grab the individual pieces directly from your .env
host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = os.getenv("UPSTASH_PASSWORD")

# 2. Forcefully construct the perfect string for Celery
REDIS_URL = f"rediss://default:{password}@{host}:{port}/0?ssl_cert_reqs=none"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Create the Celery App
# ─────────────────────────────────────────────────────────────────────────────
celery_app = Celery(
    "legal_rag_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"],  # Where our task functions live
)

# Debug: Print the Broker URL (masking password)
safe_url = REDIS_URL.split("@")[-1] if "@" in REDIS_URL else "localhost"
print(f"🔹 Celery Broker configured for: {safe_url}")

# Explicit SSL configuration for Upstash/rediss://
if REDIS_URL.startswith("rediss://"):
    celery_app.conf.update(
        broker_use_ssl={
            "ssl_cert_reqs": "none"  
        },
        redis_backend_use_ssl={
            "ssl_cert_reqs": "none"
        }
    )



# ─────────────────────────────────────────────────────────────────────────────
# 2. Configuration
# ─────────────────────────────────────────────────────────────────────────────
celery_app.conf.update(
    # Serialize tasks as JSON (human-readable, safe)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # ── Priority Queues ──────────────────────────────────────────────────────
    # Two queues: "default" for clean PDFs, "ocr" for scanned/image PDFs.
    # This prevents a slow 50-page fax from blocking a fast digital contract.
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "ocr":     {"exchange": "ocr",     "routing_key": "ocr"},
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
)
