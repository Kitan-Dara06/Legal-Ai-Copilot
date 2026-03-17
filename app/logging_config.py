# app/logging_config.py
#
# Configures Python's standard logging to emit structured JSON.
# Call configure_logging() once at FastAPI startup and at Celery worker startup.
#
# Output format (one JSON object per line):
# {"timestamp": "...", "level": "INFO", "name": "app.tasks", "message": "..."}

import logging
import sys

from pythonjsonlogger import jsonlogger


def configure_logging(level: str = "INFO") -> None:
    """Set up JSON log formatting on the root logger."""

    # Avoid double-configuration if called multiple times (e.g., hot-reload).
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)

    root.addHandler(handler)
    root.setLevel(level)

    # Quieten noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
