"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getWorkflowStatus, getWorkflowBrief, getWorkflowActions } from "@/lib/api";
import type { WorkflowStatus, DecisionBrief, DraftPayload } from "@/lib/types";

interface WorkflowState {
  status: WorkflowStatus | null;
  brief: DecisionBrief | null;
  draft: DraftPayload | null;
  loading: boolean;
  error: string | null;
}

const ACTIVE_STATUSES: WorkflowStatus[] = [
  "CLASSIFYING",
  "AWAITING_INTENT_CONFIRMATION",
  "RETRIEVING",
  "EXPANDING",
  "REASONING",
  "BRIEFING",
  "DRAFTING",
  "RECOVERING",
];

const BRIEF_READY: WorkflowStatus = "AWAITING_BRIEF_CONFIRMATION";
const DRAFT_READY: WorkflowStatus = "AWAITING_APPROVAL";
const TERMINAL: WorkflowStatus[] = ["COMPLETED", "FAILED", "ESCALATED", "CANCELLED"];

export function usePollWorkflowStatus(
  workflowId: string | null,
  token: string | null,
  orgSlug?: string,
  intervalMs = 3000,
) {
  const [state, setState] = useState<WorkflowState>({
    status: null,
    brief: null,
    draft: null,
    loading: false,
    error: null,
  });

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const fetchBrief = useCallback(async () => {
    if (!workflowId || !token) return;
    try {
      const res = await getWorkflowBrief(token, workflowId, orgSlug);
      setState((s) => ({ ...s, brief: res.brief }));
    } catch (e) {
      // brief not ready yet — silent
    }
  }, [workflowId, token, orgSlug]);

  const fetchDraft = useCallback(async () => {
    if (!workflowId || !token) return;
    try {
      const res = await getWorkflowActions(token, workflowId, orgSlug);
      const primary = res.actions.find((a) => a.draft_payload);
      if (primary?.draft_payload) {
        setState((s) => ({ ...s, draft: primary.draft_payload! }));
      }
    } catch (e) {
      // draft not ready yet — silent
    }
  }, [workflowId, token, orgSlug]);

  const poll = useCallback(async () => {
    if (!workflowId || !token) return;
    try {
      const res = await getWorkflowStatus(token, workflowId, orgSlug);
      const newStatus = res.status;
      const prev = stateRef.current.status;

      setState((s) => ({ ...s, status: newStatus, error: null }));

      // Auto-fetch brief when hitting pause 1
      if (newStatus === BRIEF_READY && prev !== BRIEF_READY) {
        await fetchBrief();
      }

      // Auto-fetch draft when hitting pause 2
      if (newStatus === DRAFT_READY && prev !== DRAFT_READY) {
        await fetchDraft();
      }

      // Stop polling on terminal states
      if (TERMINAL.includes(newStatus)) {
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      }
    } catch (e: any) {
      setState((s) => ({ ...s, error: e?.message ?? "Polling failed" }));
    }
  }, [workflowId, token, orgSlug, fetchBrief, fetchDraft]);

  useEffect(() => {
    if (!workflowId || !token) return;

    setState({ status: null, brief: null, draft: null, loading: true, error: null });
    poll().then(() => setState((s) => ({ ...s, loading: false })));

    intervalRef.current = setInterval(poll, intervalMs);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [workflowId, token, orgSlug, intervalMs, poll]);

  const refresh = useCallback(() => poll(), [poll]);

  return { ...state, refresh };
}
