"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import {
  getWorkspace, uploadDocument, deleteDocument,
  createGoal, listGoals, getGoalResult, confirmGoalIntent,
} from "@/lib/api";
import type {
  WorkspaceDetailResponse, WorkspaceDocument,
  GoalSummary, GoalIntent,
} from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import {
  Upload, FileText, Trash2, ChevronRight,
  Clock, Loader2, Sparkles, Send, CheckCircle2,
  AlertTriangle, Info, X,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import Link from "next/link";
import ReactMarkdown from "react-markdown";

// ── Document Status ───────────────────────────────────────────────────────────

function docStatusBadge(status: string) {
  if (status === "READY")      return <Badge variant="success">Ready</Badge>;
  if (status === "PROCESSING") return <Badge variant="violet">Processing</Badge>;
  if (status === "PENDING")    return <Badge variant="muted">Pending</Badge>;
  if (status === "FAILED")     return <Badge variant="danger">Failed</Badge>;
  return <Badge variant="muted">{status}</Badge>;
}

// ── Goal / workflow status chip ────────────────────────────────────────────────

function GoalStatusChip({ status }: { status: string }) {
  const actStates = ["BRIEFING","AWAITING_BRIEF_CONFIRMATION","DRAFTING","AWAITING_APPROVAL"];
  const done = status === "COMPLETED";
  const act  = actStates.includes(status);
  const fail = ["FAILED","CANCELLED","ESCALATED"].includes(status);

  return (
    <Badge
      variant={done ? "success" : act ? "gold" : fail ? "danger" : "muted"}
    >
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

// ── Workspace Detail ───────────────────────────────────────────────────────────

function WorkspaceDetailContent() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const supabase = createClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [token, setToken]           = useState<string | null>(null);
  const [orgSlug, setOrgSlug]       = useState<string | undefined>();
  const [workspace, setWorkspace]   = useState<WorkspaceDetailResponse | null>(null);
  const [goals, setGoals]           = useState<GoalSummary[]>([]);
  const [loading, setLoading]       = useState(true);
  const [uploading, setUploading]   = useState(false);
  const [goalText, setGoalText]     = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Ambiguity gate state
  const [pendingGoalId, setPendingGoalId]       = useState<string | null>(null);
  const [pendingIntent, setPendingIntent]       = useState<GoalIntent | null>(null);
  const [showAmbiguity, setShowAmbiguity]       = useState(false);
  const [intentConfidence, setIntentConfidence] = useState(0);

  // Analyze/Reason result inline display
  const [inlineResult, setInlineResult] = useState<{ type: "analyze" | "reason"; content: string } | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }: any) => {
      if (!data?.session) { router.push("/login"); return; }
      setToken(data?.session?.access_token);
      setOrgSlug(data?.session?.user?.user_metadata?.org_slug);
    });
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [ws, goalsRes] = await Promise.all([
        getWorkspace(token, params.id, orgSlug),
        listGoals(token, params.id, orgSlug).catch(() => ({ goals: [], total: 0 })),
      ]);
      setWorkspace(ws);
      setGoals(goalsRes.goals ?? []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, [token, params.id, orgSlug]);

  useEffect(() => { load(); }, [load]);

  // Upload handler
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || !token) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await uploadDocument(token, params.id, file, orgSlug);
      }
      await load();
    } catch (err) { console.error(err); }
    finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = ""; }
  };

  // Delete document
  const handleDelete = async (docId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!token) return;
    try {
      await deleteDocument(token, params.id, docId, orgSlug);
      await load();
    } catch (err) { console.error(err); }
  };

  // Submit goal
  const handleSubmit = async () => {
    if (!token || !goalText.trim() || submitting) return;

    // Pick first READY doc as primary document
    const primaryDoc = workspace?.documents.find((d) => d.status === "READY");
    if (!primaryDoc) return;

    setSubmitting(true);
    setInlineResult(null);
    try {
      const res = await createGoal(token, params.id, goalText.trim(), undefined, orgSlug);

      if (!res.goal_id) throw new Error("No goal_id returned");

      const confidence = res.intent_confidence ?? 0;
      const intent = res.primary_intent;

      // Ambiguity gate
      if (confidence < 0.8 || !intent) {
        setPendingGoalId(res.goal_id);
        setPendingIntent(intent ?? null);
        setIntentConfidence(confidence);
        setShowAmbiguity(true);
        setSubmitting(false);
        return;
      }

      // ACT → navigate to workflow review page
      if (intent === "ACT" && res.workflow_id) {
        setGoalText("");
        router.push(`/workspaces/${params.id}/workflows/${res.workflow_id}`);
        return;
      }

      // ANALYZE / REASON — poll for result inline
      if ((intent === "ANALYZE" || intent === "REASON") && res.goal_id) {
        setGoalText("");
        await pollGoalResult(res.goal_id, intent);
        await load();
      }
    } catch (err) { console.error(err); }
    finally { setSubmitting(false); }
  };

  const pollGoalResult = async (goalId: string, intent: GoalIntent) => {
    const MAX = 60;
    for (let i = 0; i < MAX; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const res = await getGoalResult(token!, params.id, goalId, orgSlug);
        if (res.status === "COMPLETED" || res.answer) {
          setInlineResult({
            type: intent.toLowerCase() as "analyze" | "reason",
            content: res.answer ?? "",
          });
          return;
        }
        if (["FAILED","ESCALATED","CANCELLED"].includes(res.status)) return;
      } catch {}
    }
  };

  const handleConfirmIntent = async (intent: GoalIntent) => {
    if (!token || !pendingGoalId) return;
    setShowAmbiguity(false);
    try {
      await confirmGoalIntent(token, params.id, pendingGoalId, intent, orgSlug);
      // After confirming, check if it became ACT
      if (intent === "ACT") {
        // poll for workflow_id
        for (let i = 0; i < 20; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          const res = await getGoalResult(token, params.id, pendingGoalId, orgSlug);
          if (res.status && ["BRIEFING","AWAITING_BRIEF_CONFIRMATION","AWAITING_APPROVAL","COMPLETED"].includes(res.status)) {
            // find workflow_id from goals list
            const fresh = await listGoals(token, params.id, orgSlug);
            const g = fresh.goals.find((g) => g.id === pendingGoalId);
            if (g?.workflow_id) {
              router.push(`/workspaces/${params.id}/workflows/${g.workflow_id}`);
              return;
            }
          }
        }
      } else {
        await pollGoalResult(pendingGoalId, intent);
        await load();
      }
    } catch (err) { console.error(err); }
    finally { setPendingGoalId(null); }
  };

  const readyDocs = workspace?.documents.filter((d) => d.status === "READY") ?? [];
  const canSubmit = readyDocs.length > 0 && goalText.trim().length > 0 && !submitting;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-[#D4A853] animate-spin" />
      </div>
    );
  }

  if (!workspace) return null;

  return (
    <div className="max-w-[1000px] mx-auto px-6 py-8">

      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs text-[#7A7A8A] mb-6">
        <Link href="/workspaces" className="hover:text-[#F0EEE9] transition-colors">Workspaces</Link>
        <span>/</span>
        <span className="text-[#F0EEE9]">{workspace.name}</span>
      </div>

      {/* Workspace header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-[#F0EEE9] tracking-tight">{workspace.name}</h1>
          {workspace.description && (
            <p className="text-sm text-[#7A7A8A] mt-1">{workspace.description}</p>
          )}
          <div className="flex items-center gap-3 mt-2">
            {workspace.intelligence_status === "READY"
              ? <Badge variant="success">Intelligence Ready</Badge>
              : <Badge variant="muted">{workspace.intelligence_status}</Badge>}
            <span className="text-xs text-[#4A4A5A]">{workspace.document_count} documents</span>
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={() => router.push("/workspaces")}>
          ← Back
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-5">

        {/* LEFT — Goal input + results + history */}
        <div className="space-y-5">

          {/* Goal input */}
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-[#F0EEE9] mb-3 flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#7C6AF7]" strokeWidth={1.5} />
              Ask a Question or Request a Draft
            </h2>
            {readyDocs.length === 0 ? (
              <div className="flex items-start gap-2 p-3 rounded-lg bg-[#E8A44C]/5 border border-[#E8A44C]/15">
                <Info className="w-4 h-4 text-[#E8A44C] mt-0.5 shrink-0" strokeWidth={1.5} />
                <p className="text-xs text-[#F0EEE9]/70">
                  Upload and process at least one document before submitting a goal.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                <textarea
                  className="w-full bg-[#1E1E28] border border-[#2A2A32] focus:border-[#7C6AF7] rounded-lg px-4 py-3 text-sm text-[#F0EEE9] placeholder-[#4A4A5A] resize-none outline-none transition-colors"
                  rows={4}
                  placeholder={`e.g. "Summarize the termination clauses" or "Draft a response to the notice in clause 15"`}
                  value={goalText}
                  onChange={(e) => setGoalText(e.target.value)}
                  disabled={submitting}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
                  }}
                  maxLength={2000}
                />
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-[#4A4A5A]">{goalText.length}/2000 · ⌘↵ to submit</span>
                  <Button variant="primary" size="sm" loading={submitting} disabled={!canSubmit} onClick={handleSubmit}>
                    <Send className="w-3.5 h-3.5" />
                    Submit
                  </Button>
                </div>
              </div>
            )}
          </Card>

          {/* Ambiguity gate */}
          {showAmbiguity && (
            <Card className="p-5 border-[#D4A853]/25 animate-slide-up" gold>
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-[#D4A853]" strokeWidth={1.5} />
                  <h3 className="text-sm font-semibold text-[#F0EEE9]">Confirm Intent</h3>
                </div>
                <button onClick={() => setShowAmbiguity(false)} className="text-[#7A7A8A] hover:text-[#F0EEE9]">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <p className="text-xs text-[#7A7A8A] mb-4">
                Confidence: <span className="text-[#D4A853] font-mono">{Math.round(intentConfidence * 100)}%</span> —
                What do you want Lex to do?
              </p>
              <div className="grid grid-cols-3 gap-2">
                {(["ANALYZE", "REASON", "ACT"] as GoalIntent[]).map((intent) => (
                  <button
                    key={intent}
                    onClick={() => handleConfirmIntent(intent)}
                    className={`p-3 rounded-lg border text-xs font-medium transition-all text-center ${
                      pendingIntent === intent
                        ? "border-[#D4A853] bg-[#D4A853]/10 text-[#D4A853]"
                        : "border-[#2A2A32] bg-[#1E1E28] text-[#7A7A8A] hover:border-[#363644] hover:text-[#F0EEE9]"
                    }`}
                  >
                    <span className="block text-base mb-1">
                      {intent === "ANALYZE" ? "📊" : intent === "REASON" ? "🔍" : "✍️"}
                    </span>
                    {intent}
                    <span className="block text-[9px] mt-0.5 opacity-60">
                      {intent === "ANALYZE" ? "Summarize" : intent === "REASON" ? "Find conflicts" : "Draft document"}
                    </span>
                  </button>
                ))}
              </div>
            </Card>
          )}

          {/* Inline result (ANALYZE / REASON) */}
          {inlineResult && (
            <Card className="p-5 animate-slide-up" violet>
              <div className="flex items-center gap-2 mb-3">
                <CheckCircle2 className="w-4 h-4 text-[#3ECFA4]" strokeWidth={1.5} />
                <span className="text-sm font-semibold text-[#F0EEE9]">
                  {inlineResult.type === "analyze" ? "Analysis Complete" : "Reasoning Complete"}
                </span>
              </div>
              <div className="prose prose-sm prose-invert max-w-none text-[#F0EEE9]/90 text-sm leading-relaxed">
                <ReactMarkdown>{inlineResult.content}</ReactMarkdown>
              </div>
              <button
                className="mt-3 text-xs text-[#7A7A8A] hover:text-[#F0EEE9] transition-colors"
                onClick={() => setInlineResult(null)}
              >
                Dismiss
              </button>
            </Card>
          )}

          {/* Goal history */}
          {goals.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-[#7A7A8A] mb-3 uppercase tracking-wider text-xs">
                Recent Goals
              </h2>
              <div className="space-y-2">
                {goals.slice(0, 8).map((goal) => (
                  <button
                    key={goal.id}
                    onClick={() => {
                      if (goal.workflow_id && ["BRIEFING","AWAITING_BRIEF_CONFIRMATION","DRAFTING","AWAITING_APPROVAL","COMPLETED"].includes(goal.status)) {
                        router.push(`/workspaces/${params.id}/workflows/${goal.workflow_id}`);
                      }
                    }}
                    className="w-full flex items-center justify-between p-4 rounded-lg border border-[#2A2A32] bg-[#16161D] hover:bg-[#1E1E28] transition-colors text-left group"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-[#F0EEE9] truncate">{goal.goal_text}</p>
                      <p className="text-[10px] text-[#4A4A5A] mt-0.5 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatDistanceToNow(new Date(goal.created_at), { addSuffix: true })}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 ml-3 shrink-0">
                      <GoalStatusChip status={goal.status} />
                      {goal.workflow_id && (
                        <ChevronRight className="w-4 h-4 text-[#4A4A5A] group-hover:text-[#7A7A8A] transition-colors" />
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT — Document Library */}
        <div>
          <Card className="overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#2A2A32]">
              <h2 className="text-sm font-semibold text-[#F0EEE9]">Documents</h2>
              <label className="cursor-pointer">
                <Button
                  variant="ghost"
                  size="sm"
                  loading={uploading}
                  onClick={() => fileInputRef.current?.click()}
                  type="button"
                >
                  <Upload className="w-3.5 h-3.5" />
                  Upload
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx"
                  multiple
                  className="hidden"
                  onChange={handleUpload}
                />
              </label>
            </div>

            {/* Document list */}
            <div className="divide-y divide-[#2A2A32]">
              {workspace.documents.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 gap-2 text-center px-4">
                  <FileText className="w-8 h-8 text-[#2A2A32]" strokeWidth={1} />
                  <p className="text-xs text-[#7A7A8A]">No documents yet</p>
                  <p className="text-[10px] text-[#4A4A5A]">Upload a PDF or DOCX to get started</p>
                </div>
              ) : (
                workspace.documents.map((doc) => (
                  <div
                    key={doc.document_id}
                    className="flex items-center justify-between px-4 py-3 hover:bg-[#1E1E28] transition-colors group"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <FileText className="w-3.5 h-3.5 text-[#7A7A8A] shrink-0" strokeWidth={1.5} />
                      <div className="min-w-0">
                        <p className="text-xs text-[#F0EEE9] truncate font-medium">{doc.filename}</p>
                        <p className="text-[10px] text-[#4A4A5A] mt-0.5">
                          {formatDistanceToNow(new Date(doc.upload_date), { addSuffix: true })}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 ml-2 shrink-0">
                      {docStatusBadge(doc.status)}
                      <button
                        onClick={(e) => handleDelete(doc.document_id, e)}
                        className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-[#7A7A8A] hover:text-[#F06B6B]"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function WorkspaceDetailPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-[#D4A853] animate-spin" />
      </div>
    }>
      <WorkspaceDetailContent />
    </Suspense>
  );
}
