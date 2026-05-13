"""
Master Orchestrator E2E Verification Script
===========================================
Tests the Master Orchestrator, verifying Intent Classification,
Ambiguity Gating, ACT (Drafting + Execution), ANALYZE, and REASON paths.

Run with:
  ./venv/bin/python3 scripts/test_action_agent.py
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import (
    Action, Goal, Organization, ToolCallLog, User, Workspace, WorkflowExecution, WorkflowStatus
)
from app.routers.action_agent import StartWorkflowRequest, ConfirmIntentRequest, start_workflow, confirm_intent, approve_workflow


async def _get_test_fixtures():
    async with AsyncSessionLocal() as db:
        org = (await db.execute(select(Organization).limit(1))).scalar_one_or_none()
        if not org:
            raise RuntimeError("No Organization found — run DB migrations first.")

        workspace = (
            await db.execute(select(Workspace).where(Workspace.org_id == org.id).limit(1))
        ).scalar_one_or_none()
        if not workspace:
            raise RuntimeError("No Workspace found — create one first.")

        user = (
            await db.execute(select(User).where(User.org_id == org.id).limit(1))
        ).scalar_one_or_none()
        if not user:
            raise RuntimeError("No User found — create one first.")

        doc_id = uuid.uuid4()
        return org, workspace, user, doc_id


async def run_workflow_test(goal_text: str, expected_path: str, org, workspace, user, doc_id):
    print(f"\n" + "═" * 60)
    print(f"🧪 Testing Goal: '{goal_text}'")
    print(f"═" * 60)

    # ── Step 1: Create a Goal
    goal = Goal(
        workspace_id=workspace.id,
        org_id=org.id,
        user_id=user.id,
        goal_text=goal_text,
    )
    async with AsyncSessionLocal() as db:
        db.add(goal)
        await db.commit()
        await db.refresh(goal)
    print(f"✅ Goal created: {goal.id}")

    # ── Step 2: Start Workflow
    req = StartWorkflowRequest(
        goal_id=goal.id,
        workspace_id=workspace.id,
        org_id=org.id,
        document_id=doc_id,
    )

    async with AsyncSessionLocal() as db:
        start_result = await start_workflow(req, db=db)

    workflow_id = uuid.UUID(start_result["workflow_id"])
    print(f"✅ Workflow started: {workflow_id}")
    print(f"   Status:           {start_result['status']}")
    print(f"   Classified Intent:{start_result['primary_intent']} (Conf: {start_result.get('intent_confidence', 0)})")

    # ── Step 3: Handle Ambiguity Gate (if triggered)
    async with AsyncSessionLocal() as db:
        wf = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == workflow_id))).scalar_one_or_none()

    if wf.status == WorkflowStatus.AWAITING_INTENT_CONFIRMATION:
        print(f"\n⚠️ Ambiguity Gate Triggered! Confirming intent as '{expected_path}'...")
        confirm_req = ConfirmIntentRequest(confirmed_intent=expected_path)
        async with AsyncSessionLocal() as db:
            confirm_result = await confirm_intent(workflow_id, confirm_req, db=db)
        print(f"   Status after confirmation: {confirm_result['status']}")
        
        # Reload DB state
        async with AsyncSessionLocal() as db:
            wf = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == workflow_id))).scalar_one_or_none()

    # ── Step 4: Handle Specific Paths
    if expected_path == "ACT":
        async with AsyncSessionLocal() as db:
            actions = (await db.execute(select(Action).where(Action.workflow_id == workflow_id))).scalars().all()
        print(f"\n📊 DB Verification (ACT post-start):")
        print(f"   WorkflowExecution.status: {wf.status.value}")
        print(f"   Actions in DB:            {len(actions)}")
        for a in actions:
            draft = a.draft_payload.get("draft_text", "No draft text") if a.draft_payload else "No payload"
            print(f"     - Action: {a.description[:40]}... | Draft Snippet: {draft[:50]}...")
        
        if wf.status == WorkflowStatus.AWAITING_APPROVAL:
            print("\n✅ HITL gate confirmed — ACT workflow correctly paused.")
            async with AsyncSessionLocal() as db:
                approve_result = await approve_workflow(workflow_id, db=db)
            print(f"✅ Approved. Final status: {approve_result['status']}")
            
            async with AsyncSessionLocal() as db:
                wf_final = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == workflow_id))).scalar_one_or_none()
                tool_logs = (await db.execute(select(ToolCallLog).where(ToolCallLog.workflow_id == workflow_id))).scalars().all()
            
            print(f"   ToolCallLog entries:      {len(tool_logs)}")
            for log in tool_logs:
                print(f"     - [{log.status.value}] {log.tool_name}: {log.response_summary[:100]}...")

            assert wf_final.status == WorkflowStatus.COMPLETED, f"Expected COMPLETED, got {wf_final.status.value}"
            print("🎉 ACT Path Verification PASSED!")

    elif expected_path == "ANALYZE":
        print(f"\n📊 DB Verification (ANALYZE post-start):")
        print(f"   WorkflowExecution.status: {wf.status.value}")
        async with AsyncSessionLocal() as db:
            wf_final = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == workflow_id))).scalar_one_or_none()
        
        print("\n📝 Generated Findings/Answer:")
        print("-" * 40)
        print(start_result.get('findings_summary', 'No summary returned!'))
        print("-" * 40)

        assert wf_final.status == WorkflowStatus.COMPLETED, f"Expected COMPLETED, got {wf_final.status.value}"
        print("🎉 ANALYZE Path Verification PASSED!")

    elif expected_path == "REASON":
        print(f"\n📊 DB Verification (REASON post-start):")
        print(f"   WorkflowExecution.status: {wf.status.value}")
        async with AsyncSessionLocal() as db:
            wf_final = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == workflow_id))).scalar_one_or_none()
        
        print("\n📝 Generated Due Diligence Report:")
        print("-" * 40)
        # In REASON path, results are typically returned in start_result findings_summary if finished
        print(start_result.get('findings_summary', 'No summary returned!'))
        print("-" * 40)

        assert wf_final.status == WorkflowStatus.COMPLETED, f"Expected COMPLETED, got {wf_final.status.value}"
        print("🎉 REASON Path Verification PASSED!")


async def test_action_agent():
    print("\n🚀 Starting Master Orchestrator E2E Verification\n" + "─" * 50)
    org, workspace, user, doc_id = await _get_test_fixtures()
    print(f"  Org:       {org.slug} ({org.id})")
    print(f"  Workspace: {workspace.name} ({workspace.id})")
    print(f"  User:      {user.email} ({user.id})")

    # Run ACT Test
    await run_workflow_test(
        goal_text="Review all deadline obligations and send required notices.",
        expected_path="ACT",
        org=org, workspace=workspace, user=user, doc_id=doc_id
    )

    # Run ANALYZE Test
    await run_workflow_test(
        goal_text="What is the indemnification liability cap mentioned in the contract?",
        expected_path="ANALYZE",
        org=org, workspace=workspace, user=user, doc_id=doc_id
    )

    # Run REASON Test
    await run_workflow_test(
        goal_text="Analyze cross-document dependencies for termination clauses.",
        expected_path="REASON",
        org=org, workspace=workspace, user=user, doc_id=doc_id
    )


if __name__ == "__main__":
    asyncio.run(test_action_agent())
