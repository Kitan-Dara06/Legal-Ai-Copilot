"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getWorkflowStatus, getWorkflowBrief, getWorkflowActions } from "@/lib/api";
import type { WorkflowStatus, DecisionBrief, DraftPayload } from "@/lib/types";

interface WorkflowState {
  status: WorkflowStatus | null;
  brief: DecisionBrief | null;
  draft: DraftPayload | null;
  downloadUrl: string | null;
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

// Max poll ticks before giving up on an active workflow (~90s at 3s interval)
const MAX_ACTIVE_TICKS = 30;
// Consecutive error threshold before stopping the poller
const MAX_CONSECUTIVE_ERRORS = 3;

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
    downloadUrl: null,
    loading: false,
    error: null,
  });

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const tickRef = useRef(0);
  const consecutiveErrorsRef = useRef(0);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

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

      // Reset error streak on success
      consecutiveErrorsRef.current = 0;

      setState((s) => ({
        ...s,
        status: newStatus,
        downloadUrl: res.download_url ?? s.downloadUrl,
        error: null,
      }));

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
        stopPolling();
        return;
      }

      // Cap polling for long-running active states
      if (ACTIVE_STATUSES.includes(newStatus)) {
        tickRef.current += 1;
        if (tickRef.current >= MAX_ACTIVE_TICKS) {
          stopPolling();
          setState((s) => ({
            ...s,
            error:
              "The workflow is taking longer than expected. The server may still be processing — refresh to check, or try again.",
          }));
        }
      }
    } catch (e: any) {
      consecutiveErrorsRef.current += 1;
      const errMsg =
        e?.code === "SERVER_ERROR"
          ? "The server encountered an error. Please try again."
          : e?.message ?? "Polling failed — please refresh.";

      setState((s) => ({ ...s, error: errMsg }));

      // Stop polling after too many consecutive errors
      if (consecutiveErrorsRef.current >= MAX_CONSECUTIVE_ERRORS) {
        stopPolling();
        setState((s) => ({
          ...s,
          error:
            "Unable to reach the server after several attempts. Please refresh the page or try again.",
        }));
      }
    }
  }, [workflowId, token, orgSlug, fetchBrief, fetchDraft, stopPolling]);

  useEffect(() => {
    if (!workflowId || !token) return;

    tickRef.current = 0;
    consecutiveErrorsRef.current = 0;
    setState({ status: null, brief: null, draft: null, downloadUrl: null, loading: true, error: null });
    poll().then(() => setState((s) => ({ ...s, loading: false })));

    intervalRef.current = setInterval(poll, intervalMs);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [workflowId, token, orgSlug, intervalMs, poll]);

  const refresh = useCallback(() => {
    tickRef.current = 0;
    consecutiveErrorsRef.current = 0;
    setState((s) => ({ ...s, loading: true, error: null }));
    return poll().finally(() => setState((s) => ({ ...s, loading: false })));
  }, [poll]);

  return { ...state, refresh };
}
