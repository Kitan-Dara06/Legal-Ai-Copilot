# app/routers/agent_query.py
"""
Agentic Query Router - session-aware.
Reads the active Redis session to scope search to selected files only.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from app.services.planner import create_execution_plan, execute_plan
from app.redis_client import get_redis_client, get_session, get_file_progress

router = APIRouter()


@router.post("/ask-agent")
async def ask_agent(
    question: str,
    session_id: Optional[str] = Query(default=None, description="Redis session ID from POST /session"),
    org_id: str = Query(default="default_org"),
    mode: str = Query(default="hybrid", description="Search strategy: hybrid, concept, or multiquery"),
    force_partial: bool = Query(default=False, description="Answer with READY files even if some are still processing"),
):
    """
    Agentic endpoint — session-scoped.

    If session_id is provided:
      - Reads file_ids from Redis session.
      - Searches ONLY those files in Qdrant (no cross-tenant leakage).
      - Returns a warning if any files are still processing.

    If no session_id:
      - Falls back to the original ChromaDB search (all files).

    Intent-based pipeline:
      - Logic/Validity  → Search + Read + Logic + Draft
      - Extraction      → Search + Read + Draft
      - General Search  → Search + Draft
    """
    print(f"\n{'=' * 60}")
    print(f"🤖 AGENT MODE: {question} (mode={mode}, session={session_id})")
    print(f"{'=' * 60}")

    # ── Session-Scoped File IDs ───────────────────────────────────────────────
    file_ids = None
    processing_warning = None

    if session_id:
        redis = get_redis_client()
        session = await get_session(session_id, redis)

        if not session:
            await redis.aclose()
            raise HTTPException(status_code=404, detail="Session not found or expired.")

        if session["org_id"] != org_id:
            await redis.aclose()
            raise HTTPException(status_code=403, detail="Session does not belong to your org.")

        ready_ids = []
        processing_files = []

        for fid_str, status in session["files"].items():
            fid = int(fid_str)
            if status == "READY":
                ready_ids.append(fid)
            elif status == "PROCESSING":
                progress = await get_file_progress(fid, redis)
                processing_files.append({"file_id": fid, "progress": progress})

        await redis.aclose()

        # If files are still processing and user hasn't forced partial
        if processing_files and not force_partial:
            descriptions = []
            for pf in processing_files:
                pct = pf["progress"]
                est = max(5, int((100 - pct) * 0.8))
                descriptions.append(f"File #{pf['file_id']} ({pct}% done, ~{est}s remaining)")

            return {
                "status": "processing",
                "message": (
                    f"I am still reading: {', '.join(descriptions)}. "
                    f"Do you want me to try with just the {len(ready_ids)} ready file(s)? "
                    f"If so, resend with force_partial=true."
                ),
                "ready_file_ids": ready_ids,
                "processing_files": processing_files,
                "answer": None,
            }

        file_ids = ready_ids
        if processing_files and force_partial:
            processing_warning = f"Note: {len(processing_files)} file(s) excluded (still processing)."

        if not file_ids:
            raise HTTPException(status_code=425, detail="No files are ready yet in this session.")

    # ── Execute Plan ──────────────────────────────────────────────────────────
    plan = create_execution_plan(question)
    result = execute_plan(plan, question, mode=mode, file_ids=file_ids, org_id=org_id)

    response = {
        "question": question,
        "search_mode": mode,
        "query_type": plan["query_type"],
        "tools_planned": [t["name"] for t in plan["tools"]],
        "trace": result["trace"],
        "answer": result["final_output"],
        "session_id": session_id,
        "files_searched": len(file_ids) if file_ids else "all",
    }

    if processing_warning:
        response["warning"] = processing_warning

    return response
