"use client";

import { Suspense, useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { getWorkspace, createWorkspaceSession } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { uploadDocument } from "@/lib/api";
import { createGoal, getGoalStatus, getWorkflowActions, listGoals } from "@/lib/api";
import { WorkspaceDetailResponse, WorkspaceDocument } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { AmbiguityGate } from "@/components/workspace/AmbiguityGate";
import { AnalyzeResult } from "@/components/workspace/AnalyzeResult";
import { ReasonResult } from "@/components/workspace/ReasonResult";
import { ActResult } from "@/components/workspace/ActResult";

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_PROGRESS: Record<string, number> = {
  PENDING: 0.2,
  PROCESSING: 0.6,
  READY: 1.0,
  FAILED: 0.0,
};

const TERMINAL_STATUSES = ["COMPLETED", "FAILED", "CANCELLED", "ESCALATED"];

// Polling backoff: first 15 polls (30s) at 2s, then settle at 10s
function getPollingInterval(pollCount: number): number {
  return pollCount < 15 ? 2000 : 10000;
}

// ── Main content (uses useSearchParams — must be inside Suspense) ─────────────

function WorkspaceDetailContent() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceId = params.id as string;

  // Auth
  const [token, setToken] = useState<string | null>(null);
  const [orgSlug, setOrgSlug] = useState<string | null>(null);

  // Workspace
  const [workspace, setWorkspace] = useState<WorkspaceDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Goal input
  const [goalText, setGoalText] = useState("");
  const [goalCharCount, setGoalCharCount] = useState(0);
  const [processing, setProcessing] = useState(false);

  // Workflow tracking — both IDs persisted in URL
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<string | null>(null);
  const [goalId, setGoalId] = useState<string | null>(null);

  // Polling state
  const pollCountRef = useRef(0);
  const errorCountRef = useRef(0);

  // Ambiguity gate
  const [showAmbiguity, setShowAmbiguity] = useState(false);
  const [detectedIntent, setDetectedIntent] = useState<string>("");
  const [intentConfidence, setIntentConfidence] = useState(0);

  // Results
  const [analyzeResult, setAnalyzeResult] = useState<any>(null);
  const [reasonResult, setReasonResult] = useState<any>(null);
  const [actResult, setActResult] = useState<any>(null);

  // Error toast
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 5000);
  };

  // ── Auth ──────────────────────────────────────────────────────────────────

  useEffect(() => {
    void (async () => {
      const supabase = createClient();
      const { data } = await supabase.auth.getSession();
      if (data.session) {
        setToken(data.session.access_token);
        setOrgSlug(localStorage.getItem("legalrag_active_org"));
      }
    })();
  }, []);

  // ── URL restoration on refresh ────────────────────────────────────────────
  // Both goalId and workflowId are stored in URL so polling + ACT panel survive refresh

  useEffect(() => {
    const urlGoalId = searchParams.get("goalId");
    const urlWorkflowId = searchParams.get("workflowId");
    if (urlGoalId && !goalId) {
      setGoalId(urlGoalId);
      setProcessing(true);
      setWorkflowStatus("PROCESSING");
      pollCountRef.current = 0;
    }
    if (urlWorkflowId && !workflowId) {
      setWorkflowId(urlWorkflowId);
    }
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Workspace loading ─────────────────────────────────────────────────────

  const loadWorkspace = useCallback(() => {
    if (!token || !workspaceId) return;
    setLoading(true);
    getWorkspace(token, workspaceId, orgSlug || undefined)
      .then(setWorkspace)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, workspaceId, orgSlug]);

  useEffect(() => {
    loadWorkspace();
  }, [loadWorkspace]);

  // ── Polling with backoff + retry ──────────────────────────────────────────

  useEffect(() => {
    if (!goalId || !token) return;

    const tick = async () => {
      try {
        const res = await getGoalStatus(token, workspaceId, goalId, orgSlug || undefined);
        errorCountRef.current = 0; // reset error streak on success
        pollCountRef.current += 1;

        setWorkflowStatus(res.status);

        // Fetch ACT actions when workflow is awaiting approval
        const workflows = (res as any).workflows as any[] | undefined;
        if (res.status === "AWAITING_APPROVAL" && workflows && workflows.length > 0) {
          const wfId = workflows[0].id;
          if (wfId !== workflowId) setWorkflowId(wfId);
          try {
            const actData = await getWorkflowActions(token, wfId, orgSlug || undefined);
            setActResult({ actions: actData.actions, workflow_id: wfId });
          } catch (e) {
            console.error("Failed to fetch actions:", e);
          }
        }

        // Terminal: stop polling, clear URL
        if (TERMINAL_STATUSES.includes(res.status)) {
          clearInterval(intervalRef.current!);
          setProcessing(false);
          loadWorkspace();
          router.replace(`/workspaces/${workspaceId}`, { scroll: false });
        }
      } catch {
        errorCountRef.current += 1;
        // Allow up to 3 consecutive network errors before giving up
        if (errorCountRef.current >= 3) {
          clearInterval(intervalRef.current!);
          setProcessing(false);
          showToast("Lost connection to server. Refresh to resume.");
        }
      }
    };

    // Dynamic interval: start fast, slow down after 30s
    const intervalRef = { current: null as ReturnType<typeof setInterval> | null };
    const schedule = () => {
      intervalRef.current = setInterval(tick, getPollingInterval(pollCountRef.current));
    };
    schedule();

    // Re-schedule when poll count crosses the backoff threshold
    const backoffWatcher = setInterval(() => {
      if (pollCountRef.current === 15) {
        clearInterval(intervalRef.current!);
        schedule();
      }
    }, 1000);

    return () => {
      clearInterval(intervalRef.current!);
      clearInterval(backoffWatcher);
    };
  }, [goalId, token, workspaceId, orgSlug, loadWorkspace]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length || !token) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await uploadDocument(token, workspaceId, file, orgSlug || undefined);
      }
      loadWorkspace();
    } catch (err) {
      showToast("Upload failed. Please try again.");
      console.error("Upload failed:", err);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleCreateSession = async () => {
    if (!token || !workspace) return;
    const readyDocs = workspace.documents.filter((d) => d.status === "READY");
    if (readyDocs.length === 0) return;
    try {
      const res = await createWorkspaceSession(
        token,
        workspaceId,
        readyDocs.map((d) => d.document_id),
        orgSlug || undefined,
      );
      setSessionId(res.session_id);
    } catch (err) {
      showToast("Session creation failed.");
      console.error("Session creation failed:", err);
    }
  };

  const handleSubmitGoal = async () => {
    if (!token || !goalText.trim()) return;
    setProcessing(true);
    setAnalyzeResult(null);
    setReasonResult(null);
    setShowAmbiguity(false);
    setWorkflowId(null);
    setWorkflowStatus(null);
    setGoalId(null);
    pollCountRef.current = 0;
    errorCountRef.current = 0;

    try {
      const res = await createGoal(
        token,
        workspaceId,
        goalText.trim(),
        undefined,
        orgSlug || undefined,
      );

      // Build URL params — store both IDs for refresh resilience
      const params = new URLSearchParams();
      if (res.goal_id) {
        setGoalId(res.goal_id);
        params.set("goalId", res.goal_id);
      }
      if (res.workflow_id) {
        setWorkflowId(res.workflow_id);
        setWorkflowStatus(res.status || "PROCESSING");
        params.set("workflowId", res.workflow_id);
      }
      if (params.toString()) {
        router.replace(`/workspaces/${workspaceId}?${params.toString()}`, { scroll: false });
      }

      if (res.primary_intent && res.intent_confidence !== undefined) {
        if (res.intent_confidence < 0.8 && res.primary_intent) {
          setDetectedIntent(res.primary_intent);
          setIntentConfidence(res.intent_confidence);
          setShowAmbiguity(true);
        }
      }

      if (res.answer) {
        setAnalyzeResult({
          answer: res.answer,
          confidence: res.intent_confidence || 0.5,
          source_citations: [],
          faithfulness_score: 1.0,
        });
      }

      setGoalText("");
    } catch (err) {
      showToast("Failed to submit goal. Please try again.");
      console.error("Goal creation failed:", err);
      setProcessing(false);
    }
  };

  const handleConfirmIntent = (confirmedIntent: string) => {
    setShowAmbiguity(false);
    setWorkflowStatus("EXECUTING");
  };

  // ── Status helpers ────────────────────────────────────────────────────────

  const statusVariant = (
    status: string,
  ): "success" | "info" | "warning" | "error" | "default" => {
    switch (status) {
      case "READY":
        return "success";
      case "PROCESSING":
        return "info";
      case "PENDING":
        return "warning";
      case "FAILED":
        return "error";
      default:
        return "default";
    }
  };

  // ── Loading & error states ────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Skeleton loader */}
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-slate-800 rounded w-1/3" />
          <div className="h-4 bg-slate-800 rounded w-1/4" />
          <div className="h-48 bg-slate-800 rounded mt-6" />
          <div className="h-32 bg-slate-800 rounded" />
        </div>
      </div>
    );
  }

  if (!workspace) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="text-slate-400 text-center py-12">Workspace not found</div>
      </div>
    );
  }

  const readyDocs = workspace.documents.filter((d) => d.status === "READY");

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Global toast */}
      {toastMessage && (
        <div className="fixed top-4 right-4 z-50 bg-red-900/90 border border-red-500/40 text-red-200 text-sm px-4 py-3 rounded-lg shadow-lg max-w-sm">
          {toastMessage}
        </div>
      )}

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">{workspace.name}</h1>
        {workspace.description && (
          <p className="text-slate-400 text-sm mt-1">{workspace.description}</p>
        )}
        <div className="flex items-center gap-3 mt-2">
          <Badge variant={statusVariant(workspace.intelligence_status)}>
            {workspace.intelligence_status}
          </Badge>
          <span className="text-slate-400 text-sm">
            {workspace.document_count} documents
          </span>
        </div>
      </div>

      {/* AWAITING_APPROVAL banner — prominent, actionable */}
      {workflowStatus === "AWAITING_APPROVAL" && (
        <div className="mb-6 p-4 bg-amber-900/20 border border-amber-500/40 rounded-lg flex items-center justify-between">
          <div>
            <p className="text-amber-300 font-semibold text-sm">
              ⚖️ Action requires your approval
            </p>
            <p className="text-amber-500/80 text-xs mt-0.5">
              Lex has drafted a notice. Review and approve or reject it to continue.
            </p>
          </div>
          <Link
            href="/approvals"
            className="flex-shrink-0 ml-4 px-4 py-2 bg-amber-500 hover:bg-amber-400 text-black text-sm font-semibold rounded-lg transition-colors"
          >
            Go to Approvals →
          </Link>
        </div>
      )}

      {/* Document Library */}
      <Card className="mb-6 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Document Library</h2>
          <label className="cursor-pointer">
            <span
              className={`inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors px-4 py-2 ${uploading
                  ? "bg-slate-700 text-slate-400 cursor-not-allowed"
                  : "bg-accent-blue text-white hover:bg-accent-blue/90"
                }`}
            >
              {uploading ? "Uploading..." : "Upload PDF"}
            </span>
            <input
              type="file"
              accept=".pdf,.docx"
              multiple
              className="hidden"
              onChange={handleUpload}
            />
          </label>
        </div>
        {workspace.documents.length === 0 ? (
          <p className="text-slate-500 text-sm py-4">
            No documents yet. Upload a PDF to get started.
          </p>
        ) : (
          <div className="space-y-2">
            {workspace.documents.map((doc) => (
              <div
                key={doc.document_id}
                className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg"
              >
                <div className="flex items-center gap-3">
                  <span className="text-slate-300 text-sm">{doc.filename}</span>
                  {doc.stages && (
                    <div className="flex gap-1">
                      {Object.entries(doc.stages).map(([stage, done]) => (
                        <Badge
                          key={stage}
                          variant={done ? "success" : "warning"}
                        >
                          {stage}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={statusVariant(doc.status)}>{doc.status}</Badge>
                  {doc.status === "FAILED" && doc.error && (
                    <span className="text-red-400 text-xs" title={doc.error}>
                      ⚠
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Active Session Panel */}
      <Card className="mb-6 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Active Session</h2>
          {!sessionId && readyDocs.length > 0 && (
            <Button onClick={handleCreateSession} size="sm">
              Initialize Session ({readyDocs.length} ready docs)
            </Button>
          )}
        </div>
        {sessionId ? (
          <div>
            <Badge variant="success">Session Active</Badge>
            <span className="text-slate-400 text-xs ml-2 font-mono">
              {sessionId.slice(0, 8)}...
            </span>
            <p className="text-slate-500 text-sm mt-2">
              {readyDocs.length} documents in context
            </p>
          </div>
        ) : (
          <p className="text-slate-500 text-sm">
            {readyDocs.length === 0
              ? "No documents ready. Wait for processing to complete."
              : "Select ready documents and initialize a session to start querying."}
          </p>
        )}
      </Card>

      {/* Goal Input */}
      {sessionId && (
        <Card className="mb-6 p-4">
          <h2 className="text-lg font-semibold text-white mb-4">What do you need?</h2>
          <div className="relative">
            <textarea
              className="w-full bg-slate-800 border border-slate-700 rounded-lg p-4 text-white placeholder-slate-500 resize-none focus:outline-none focus:border-blue-500"
              rows={3}
              placeholder="What do you need?"
              value={goalText}
              disabled={processing}
              onChange={(e) => {
                setGoalText(e.target.value);
                setGoalCharCount(e.target.value.length);
              }}
              maxLength={2000}
            />
            <div className="absolute bottom-3 right-3 text-xs text-slate-500">
              {goalCharCount}/2000
            </div>
          </div>
          <div className="flex justify-end mt-3">
            <Button onClick={handleSubmitGoal} disabled={!goalText.trim() || processing}>
              {processing ? "Processing..." : "Submit"}
            </Button>
          </div>
        </Card>
      )}

      {/* Workflow Status spinner — hide when awaiting approval (banner covers it) */}
      {workflowStatus &&
        workflowStatus !== "COMPLETED" &&
        workflowStatus !== "FAILED" &&
        workflowStatus !== "AWAITING_APPROVAL" && (
          <Card className="mb-6 p-4">
            <div className="flex items-center gap-3">
              <div className="animate-spin h-4 w-4 border-2 border-blue-500 border-t-transparent rounded-full" />
              <span className="text-slate-300 text-sm">{workflowStatus}</span>
              {pollCountRef.current >= 15 && (
                <span className="text-slate-500 text-xs">
                  (checking every 10s — this may take a few minutes)
                </span>
              )}
            </div>
          </Card>
        )}

      {/* Ambiguity Gate */}
      {showAmbiguity && (
        <AmbiguityGate
          detectedIntent={detectedIntent}
          confidence={intentConfidence}
          onConfirm={handleConfirmIntent}
        />
      )}

      {/* Analyze Result */}
      {analyzeResult && <AnalyzeResult result={analyzeResult} />}

      {/* Reason Result */}
      {reasonResult && <ReasonResult result={reasonResult} />}

      {/* Act Result */}
      {actResult && (
        <ActResult actions={actResult.actions} workflowId={actResult.workflow_id} />
      )}
    </div>
  );
}

// ── Page export — Suspense required for useSearchParams in Next.js 14 ─────────

export default function WorkspaceDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-6xl mx-auto px-4 py-8">
          <div className="animate-pulse space-y-4">
            <div className="h-8 bg-slate-800 rounded w-1/3" />
            <div className="h-4 bg-slate-800 rounded w-1/4" />
            <div className="h-48 bg-slate-800 rounded mt-6" />
            <div className="h-32 bg-slate-800 rounded" />
          </div>
        </div>
      }
    >
      <WorkspaceDetailContent />
    </Suspense>
  );
}
