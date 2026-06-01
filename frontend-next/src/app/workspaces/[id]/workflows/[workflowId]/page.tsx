"use client";

import { Suspense } from "react";
import { useParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { useEffect, useState } from "react";
import {
  confirmBrief,
  approveWorkflow,
  rejectWorkflow,
} from "@/lib/api";
import { usePollWorkflowStatus } from "@/hooks/usePollWorkflowStatus";
import { PipelineTracker } from "@/components/workflow/PipelineTracker";
import { BriefReview } from "@/components/workflow/BriefReview";
import { DraftReview } from "@/components/workflow/DraftReview";
import { Badge } from "@/components/ui/Badge";
import { Loader2, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import Link from "next/link";

function WorkflowPageContent() {
  const params = useParams<{ id: string; workflowId: string }>();
  const router = useRouter();
  const supabase = createClient();

  const [token, setToken] = useState<string | null>(null);
  const [orgSlug, setOrgSlug] = useState<string | undefined>();
  // Optimistic transitioning flag — hides the HITL panel immediately on click
  const [transitioning, setTransitioning] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }: any) => {
      if (!data?.session) { router.push("/login"); return; }
      setToken(data?.session?.access_token);
      setOrgSlug(data?.session?.user?.user_metadata?.org_slug);
    });
  }, []);

  const { status, brief, draft, loading, error, refresh } = usePollWorkflowStatus(
    params.workflowId,
    token,
    orgSlug,
  );

  const handleProceed = async () => {
    if (!token) return;
    setTransitioning(true);
    try {
      await confirmBrief(token, params.workflowId, true, orgSlug);
    } catch (e) {
      setTransitioning(false);
      return;
    }
    // Poll until status moves away from AWAITING_BRIEF_CONFIRMATION
    const wait = () => new Promise((r) => setTimeout(r, 2000));
    for (let i = 0; i < 30; i++) {
      await wait();
      await refresh();
      if (status !== "AWAITING_BRIEF_CONFIRMATION") break;
    }
    setTransitioning(false);
  };

  const handleAbort = async () => {
    if (!token) return;
    await confirmBrief(token, params.workflowId, false, orgSlug);
    router.push(`/workspaces/${params.id}`);
  };

  const handleApprove = async () => {
    if (!token) return;
    setTransitioning(true);
    try {
      await approveWorkflow(token, params.workflowId, orgSlug);
    } catch (e) {
      setTransitioning(false);
      return;
    }
    setTransitioning(false);
    refresh();
  };

  const handleReject = async (reason: string) => {
    if (!token) return;
    await rejectWorkflow(token, params.workflowId, reason, orgSlug);
    refresh();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-[#D4A853] animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs text-[#7A7A8A] mb-6">
        <Link href="/workspaces" className="hover:text-[#F0EEE9] transition-colors">Workspaces</Link>
        <span>/</span>
        <Link href={`/workspaces/${params.id}`} className="hover:text-[#F0EEE9] transition-colors">
          Workspace
        </Link>
        <span>/</span>
        <span className="text-[#F0EEE9] font-mono">{params.workflowId.slice(0, 8)}…</span>
      </div>

      {/* Pipeline Tracker */}
      <PipelineTracker status={status} />

      {/* ── State-driven content ── */}

      {/* Spinning while processing or transitioning between HITL steps */}
      {(transitioning || (status && !["AWAITING_BRIEF_CONFIRMATION", "AWAITING_APPROVAL", "COMPLETED", "FAILED", "ESCALATED", "CANCELLED"].includes(status))) && (
        <div className="flex flex-col items-center justify-center py-20 gap-4 animate-fade-in">
          <Loader2 className="w-10 h-10 text-[#D4A853] animate-spin-slow" />
          <div className="text-center">
            <p className="text-[#F0EEE9] font-medium">
              {transitioning ? "Submitting…" : humanStatus(status ?? "")}
            </p>
            <p className="text-sm text-[#7A7A8A] mt-1">This usually takes 30–90 seconds…</p>
          </div>
        </div>
      )}

      {/* HITL Pause 1 — Brief */}
      {!transitioning && status === "AWAITING_BRIEF_CONFIRMATION" && brief && (
        <BriefReview
          brief={brief}
          workflowId={params.workflowId}
          onProceed={handleProceed}
          onAbort={handleAbort}
        />
      )}

      {/* HITL Pause 2 — Draft */}
      {status === "AWAITING_APPROVAL" && draft && (
        <DraftReview
          draft={draft}
          workflowId={params.workflowId}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}

      {/* Completed */}
      {status === "COMPLETED" && (
        <div className="flex flex-col items-center justify-center py-20 gap-4 animate-slide-up">
          <div className="w-16 h-16 rounded-full bg-[#3ECFA4]/10 border border-[#3ECFA4]/25 flex items-center justify-center">
            <CheckCircle2 className="w-8 h-8 text-[#3ECFA4]" />
          </div>
          <div className="text-center">
            <h3 className="text-xl font-semibold text-[#F0EEE9]">Workflow Complete</h3>
            <p className="text-sm text-[#7A7A8A] mt-1">
              Your DOCX has been generated and is ready for download.
            </p>
          </div>
          <Badge variant="success">COMPLETED</Badge>
        </div>
      )}

      {/* Failed */}
      {(status === "FAILED" || status === "CANCELLED") && (
        <div className="flex flex-col items-center justify-center py-20 gap-4 animate-slide-up">
          <div className="w-16 h-16 rounded-full bg-[#F06B6B]/10 border border-[#F06B6B]/25 flex items-center justify-center">
            <XCircle className="w-8 h-8 text-[#F06B6B]" />
          </div>
          <div className="text-center">
            <h3 className="text-xl font-semibold text-[#F0EEE9]">{status === "CANCELLED" ? "Workflow Cancelled" : "Workflow Failed"}</h3>
            <p className="text-sm text-[#7A7A8A] mt-1">
              {status === "CANCELLED"
                ? "You aborted this workflow."
                : "Something went wrong. Check escalations for details."}
            </p>
          </div>
        </div>
      )}

      {/* Escalated */}
      {status === "ESCALATED" && (
        <div className="flex flex-col items-center justify-center py-20 gap-4 animate-slide-up">
          <div className="w-16 h-16 rounded-full bg-[#E8A44C]/10 border border-[#E8A44C]/25 flex items-center justify-center">
            <AlertTriangle className="w-8 h-8 text-[#E8A44C]" />
          </div>
          <div className="text-center">
            <h3 className="text-xl font-semibold text-[#F0EEE9]">Escalated</h3>
            <p className="text-sm text-[#7A7A8A] mt-1">
              Maximum revision cycles reached. View in Escalations.
            </p>
          </div>
          <Link
            href="/escalations"
            className="text-sm text-[#E8A44C] hover:underline"
          >
            View Escalations →
          </Link>
        </div>
      )}

      {error && (
        <p className="text-xs text-[#F06B6B] text-center mt-4">{error}</p>
      )}
    </div>
  );
}

function humanStatus(s: string): string {
  const map: Record<string, string> = {
    CLASSIFYING: "Classifying your request…",
    AWAITING_INTENT_CONFIRMATION: "Waiting for intent confirmation…",
    RETRIEVING: "Retrieving relevant clauses…",
    EXPANDING: "Expanding knowledge graph…",
    REASONING: "Reasoning over contract…",
    BRIEFING: "Building decision brief…",
    DRAFTING: "Generating draft…",
    RECOVERING: "Recovering from error…",
    REVISING: "Revising draft…",
  };
  return map[s] ?? s;
}

export default function WorkflowPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-[#D4A853] animate-spin" />
      </div>
    }>
      <WorkflowPageContent />
    </Suspense>
  );
}
