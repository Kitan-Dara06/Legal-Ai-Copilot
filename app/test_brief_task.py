"""
Celery task to run the decision brief test inside the worker process.
Run this from the API droplet or from a python shell:

    from app.celery_app import celery_app
    celery_app.send_task("app.tasks.test_decision_brief", kwargs={
        "org_id": "...",
        "workspace_id": "...",
        "goal": "Draft a response...",
        "filename": "sec_filing_ex10u.pdf",
    })
"""

import json
import logging
import os
import sys

log = logging.getLogger(__name__)

# Add scripts dir to path so we can import the test module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.celery_app import celery_app


@celery_app.task(
    name="app.tasks.test_decision_brief",
    bind=True,
    max_retries=0,
    acks_late=True,
    soft_time_limit=120,
    time_limit=150,
)
def test_decision_brief(
    self,
    org_id: str,
    workspace_id: str,
    goal: str,
    filename: str | None = None,
    top_k: int = 8,
):
    """
    Run the decision brief test pipeline inside the warm worker.
    SPLADE + voyage are already loaded in this process.
    """
    import asyncio

    log.info("[test_brief] Starting: goal=%.60s", goal)

    # Test that AuditLogger works inside the worker process
    try:
        from app.services.audit.logger import AuditLogger
        from app.services.audit.schemas import CorrelationContext

        AuditLogger.node_enter(
            "test_brief_run", correlation=CorrelationContext(workflow_id=goal[:30])
        )
        log.info("[test_brief] AuditLogger.node_enter OK")
    except Exception as e:
        log.warning("[test_brief] AuditLogger test failed: %s", e)

    # Import the test module (lazy, using importlib since scripts/ is not a package)
    import importlib.util

    _scripts_path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "test_decision_brief.py"
    )
    _spec = importlib.util.spec_from_file_location("test_decision_brief", _scripts_path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _retrieve = _mod._retrieve
    _generate_brief = _mod._generate_brief
    _print_brief = _mod._print_brief

    # Step 1: Retrieve chunks (uses already-warm SPLADE + voyage)
    chunks = _retrieve(
        goal=goal,
        org_id=org_id,
        workspace_id=workspace_id,
        filename=filename,
        top_k=top_k,
    )

    if not chunks:
        log.warning("[test_brief] No chunks retrieved — check params")
        return {"status": "no_chunks", "goal": goal}

    # Step 2: Generate brief (LLM call)
    brief = asyncio.run(_generate_brief(goal=goal, chunks=chunks))

    # Step 3: Print + save
    _print_brief(brief, chunks)

    # Log node_exit
    try:
        AuditLogger.node_exit(
            "test_brief_run",
            duration_ms=20_000,
            correlation=CorrelationContext(workflow_id=goal[:30]),
        )
        log.info("[test_brief] AuditLogger.node_exit OK")
    except Exception as e:
        log.warning("[test_brief] AuditLogger node_exit failed: %s", e)

    out_path = f"/tmp/brief_{goal[:20].replace(' ', '_')}.json"
    with open(out_path, "w") as f:
        json.dump(brief.model_dump(mode="json"), f, indent=2, default=str)

    log.info("[test_brief] Complete. Written to %s", out_path)

    return {
        "status": "ok",
        "goal": goal,
        "confidence": brief.confidence,
        "proceed": brief.proceed_recommended,
        "actions": len(brief.recommended_actions),
        "clauses": len(brief.relevant_clauses),
        "output": out_path,
    }
