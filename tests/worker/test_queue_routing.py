"""
Tests for queue routing — tasks must go to their designated queues.

Routing is defined in app/worker.py via ``task_routes``.  The Celery app
is imported from ``app.worker`` so that the worker-only configuration
(queues, routes, beat schedule) is applied before inspection.
"""

import pytest

from app.worker import celery_app


class TestQueueRouting:
    """Each task type must be routed to its designated queue."""

    # ── Task → queue mapping (via task_routes) ────────────────────────

    def test_digital_pdf_routes_to_default_queue(self):
        """process_digital_pdf should route to 'default' queue."""
        route = celery_app.conf.task_routes.get("app.tasks.process_digital_pdf", {})
        assert route.get("queue") == "default"

    def test_scanned_pdf_routes_to_ocr_queue(self):
        """process_scanned_pdf should route to 'ocr' queue."""
        route = celery_app.conf.task_routes.get("app.tasks.process_scanned_pdf", {})
        assert route.get("queue") == "ocr"

    def test_deadline_scanner_routes_to_deadline_queue(self):
        """deadline_scanner should route to 'deadline' queue."""
        route = celery_app.conf.task_routes.get("app.tasks.deadline_scanner", {})
        assert route.get("queue") == "deadline"

    def test_cleanup_routes_to_default_queue(self):
        """cleanup_stale_data should route to 'default' queue."""
        route = celery_app.conf.task_routes.get("app.tasks.cleanup_stale_data", {})
        assert route.get("queue") == "default"

    def test_resolve_defined_terms_routes_to_default(self):
        """resolve_defined_term_conflicts should route to 'default' queue."""
        route = celery_app.conf.task_routes.get(
            "app.tasks.resolve_defined_term_conflicts", {}
        )
        assert route.get("queue") == "default"

    def test_resolve_deadline_conflicts_routes_to_deadline(self):
        """resolve_deadline_conflicts should route to 'deadline' queue."""
        route = celery_app.conf.task_routes.get(
            "app.tasks.resolve_deadline_conflicts", {}
        )
        assert route.get("queue") == "deadline"

    # ── Queue definitions ─────────────────────────────────────────────

    def test_queues_are_configured(self):
        """All three queues must be defined in task_queues."""
        queues = celery_app.conf.task_queues
        assert "default" in queues, "Missing 'default' queue"
        assert "ocr" in queues, "Missing 'ocr' queue"
        assert "deadline" in queues, "Missing 'deadline' queue"

    def test_queue_default_exchange_and_key(self):
        """Each queue should have a matching exchange + routing_key."""
        for name in ("default", "ocr", "deadline"):
            q = celery_app.conf.task_queues[name]
            assert q["exchange"] == name, f"Queue '{name}' exchange should match name"
            assert q["routing_key"] == name, (
                f"Queue '{name}' routing_key should match name"
            )

    # ── Default queue / routing key ───────────────────────────────────

    def test_default_routing_key(self):
        """Default queue and routing key should both be 'default'."""
        assert celery_app.conf.task_default_queue == "default"
        assert celery_app.conf.task_default_routing_key == "default"

    # ── Beat schedule ─────────────────────────────────────────────────

    def test_beat_schedule_includes_deadline_scanner(self):
        """deadline_scanner must be scheduled every 15 minutes."""
        beat_schedule = celery_app.conf.beat_schedule
        entry = beat_schedule.get("deadline-scanner-every-15-min")
        assert entry is not None, "Missing deadline_scanner beat entry"
        assert entry["task"] == "app.tasks.deadline_scanner"
        assert entry["options"]["queue"] == "deadline"
