"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import {
  getWorkspace, uploadDocument, deleteDocument,
  createGoal, listGoals, getGoalResult, confirmGoalIntent,
  createSession,
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

function safeFormatDistance(dateStr: string | null | undefined) {
  if (!dateStr) return "recently";
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return "recently";
    return formatDistanceToNow(d, { addSuffix: true });
  } catch {
    return "recently";
  }
}

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
  const [submitError, setSubmitError]   = useState<string | null>(null);

  // Document selection
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [hasInitializedDocs, setHasInitializedDocs] = useState(false);

  // Auto-select all READY documents ONCE on first load of the workspace
  useEffect(() => {
    if (!workspace || hasInitializedDocs) return;
    const readyIds = workspace.documents
      .filter((d) => d.status === "READY")
      .map((d) => d.document_id);
      
    if (readyIds.length > 0) {
      setSelectedDocIds(readyIds);
      setHasInitializedDocs(true);
    }
  }, [workspace, hasInitializedDocs]);

  useEffect(() => {
    // Get initial session
    supabase.auth.getSession().then(({ data }: any) => {
      if (!data?.session) { router.push("/login"); return; }
      setToken(data.session.access_token);
      setOrgSlug(data.session.user?.user_metadata?.org_slug);
    });

    // Keep token fresh whenever Supabase silently refreshes it
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event: string, session: any) => {
        if (!session) { router.push("/login"); return; }
        setToken(session.access_token);
        setOrgSlug(session.user?.user_metadata?.org_slug);
      },
    );
    return () => subscription.unsubscribe();
  }, []);

  const [goalsError, setGoalsError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setGoalsError(null);
    try {
      const ws = await getWorkspace(token, params.id, orgSlug);
      setWorkspace(ws);
    } catch (e: any) {
      if (e?.code === "AUTH_EXPIRED") { router.push("/login"); return; }
      console.error("Failed to load workspace:", e);
    }
    try {
      const goalsRes = await listGoals(token, params.id, orgSlug);
      setGoals(goalsRes.goals ?? []);
    } catch (e: any) {
      if (e?.code === "AUTH_EXPIRED") { router.push("/login"); return; }
      setGoalsError("Could not load conversation history.");
      console.error("Failed to load goals:", e);
    }
    setLoading(false);
  }, [token, params.id, orgSlug]);

  useEffect(() => { load(); }, [load]);

  // Poll workspace state every 3 seconds while any document is PENDING or PROCESSING
  useEffect(() => {
    if (!workspace || !token || loading) return;

    const hasActiveDocs = workspace.documents.some(
      (doc) => doc.status === "PENDING" || doc.status === "PROCESSING"
    );

    if (!hasActiveDocs) return;

    const interval = setInterval(() => {
      load();
    }, 3000);

    return () => clearInterval(interval);
  }, [workspace, token, loading, load]);

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
    if (!goalText.trim() || submitting || selectedDocIds.length === 0) return;

    // Always get a fresh token from Supabase before making API calls
    const { data: sessionData } = await supabase.auth.getSession();
    if (!sessionData?.session) { router.push("/login"); return; }
    const freshToken = sessionData.session.access_token;
    setToken(freshToken);

    setSubmitting(true);
    setInlineResult(null);
    setSubmitError(null);
    const submittedText = goalText.trim();
    setGoalText("");

    try {
      const sessionRes = await createSession(freshToken, params.id, selectedDocIds, orgSlug);
      if (!sessionRes.session_id) throw new Error("Failed to create session");

      const res = await createGoal(freshToken, params.id, submittedText, sessionRes.session_id, orgSlug);
      if (!res.goal_id) throw new Error("No goal_id returned");

      const confidence = res.intent_confidence ?? 0;
      const intent = res.intent;

      // Refresh history immediately so the new entry shows without a manual refresh
      load();

      // Ambiguity gate — only show when we genuinely don't know the intent
      if (confidence < 0.8 || !intent) {
        setPendingGoalId(res.goal_id);
        setPendingIntent(intent ?? null);
        setIntentConfidence(confidence);
        setShowAmbiguity(true);
        setSubmitting(false);
        return;
      }

      // ACT → navigate to workflow page for HITL review
      if (intent === "ACT" && res.workflow_id) {
        router.push(`/workspaces/${params.id}/workflows/${res.workflow_id}`);
        return;
      }

      // ANALYZE / REASON — poll until done, then refresh history again
      if ((intent === "ANALYZE" || intent === "REASON") && res.goal_id) {
        const gotResult = await pollGoalResult(res.goal_id, intent, freshToken);
        if (!gotResult) setSubmitError("Lex couldn't generate a response. Try again.");
        await load();
      }
    } catch (err: any) {
      setGoalText(submittedText); // restore text if submission failed
      if (err?.code === "AUTH_EXPIRED") {
        router.push("/login");
        return;
      }
      setSubmitError(err?.message || "Something went wrong. Please try again.");
    } finally { setSubmitting(false); }
  };

  const pollGoalResult = async (goalId: string, intent: GoalIntent, freshToken?: string): Promise<boolean> => {
    const t = freshToken || token;
    const MAX = 20; // 1 min max (~20 × 3s polls)
    for (let i = 0; i < MAX; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const res = await getGoalResult(t!, params.id, goalId, orgSlug);
        if (res.status === "COMPLETED" || res.answer) {
          setInlineResult({
            type: intent.toLowerCase() as "analyze" | "reason",
            content: res.answer ?? "",
          });
          return true;
        }
        // Backend crashed or workflow explicitly failed — stop polling immediately
        if (["FAILED", "ESCALATED", "CANCELLED"].includes(res.status)) {
          setSubmitError("The workflow failed on the server. Please try again.");
          return false;
        }
      } catch (err: any) {
        if (err?.code === "AUTH_EXPIRED") { router.push("/login"); return false; }
        // 5xx — server is down, no point continuing to poll
        if (err?.code === "SERVER_ERROR") {
          setSubmitError("The server encountered an error. Please try again in a moment.");
          return false;
        }
      }
    }
    // Timed out after ~1 minute
    setSubmitError("Request timed out. The server may still be processing — refresh to check, or try again.");
    return false;
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
  const canSubmit = selectedDocIds.length > 0 && goalText.trim().length > 0 && !submitting;

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
                <p className="text-xs text-[#F0EEE9]/70">Upload and process at least one document before asking questions.</p>
              </div>
            ) : (
              <div className="space-y-3">
                <textarea
                  className="w-full bg-[#1E1E28] border border-[#2A2A32] focus:border-[#7C6AF7] rounded-lg px-4 py-3 text-sm text-[#F0EEE9] placeholder-[#4A4A5A] resize-none outline-none transition-colors"
                  rows={4}
                  placeholder={`e.g. "Summarize the termination clauses" or "Draft a response to the notice in clause 15"`}
                  value={goalText}
                  onChange={(e) => { setGoalText(e.target.value); setSubmitError(null); }}
                  disabled={submitting}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(); }}
                  maxLength={2000}
                />
                {selectedDocIds.length === 0 && goalText.trim().length > 0 && (
                  <div className="flex items-center gap-2 text-[11px] text-[#E8A44C]">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                    Select at least one document on the right before submitting.
                  </div>
                )}
                {submitError && (
                  <div className="flex items-start gap-2 p-2.5 rounded-lg bg-[#F06B6B]/5 border border-[#F06B6B]/20">
                    <AlertTriangle className="w-3.5 h-3.5 text-[#F06B6B] mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-[#F06B6B]">{submitError}</p>
                      <button
                        className="text-[10px] text-[#F06B6B]/70 hover:text-[#F06B6B] mt-1 underline underline-offset-2 transition-colors"
                        onClick={() => {
                          setSubmitError(null);
                          setGoalText(goalText || "");
                        }}
                      >
                        Dismiss &amp; try again
                      </button>
                    </div>
                  </div>
                )}
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

          {/* Conversation history — chat log */}
          {goalsError && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-[#F06B6B]/5 border border-[#F06B6B]/20">
              <AlertTriangle className="w-4 h-4 text-[#F06B6B] shrink-0" />
              <p className="text-xs text-[#F06B6B]">{goalsError}</p>
            </div>
          )}
          {goals.length > 0 && (
            <div>
              <h2 className="text-xs font-semibold text-[#7A7A8A] mb-3 uppercase tracking-wider">
                Conversation History
              </h2>
              <div className="space-y-3">
                {goals.slice(0, 20).map((goal) => (
                  <div key={goal.id} className="rounded-xl border border-[#2A2A32] bg-[#16161D] overflow-hidden">
                    {/* Question bubble */}
                    <div className="flex items-start gap-3 p-4">
                      <div className="w-6 h-6 rounded-full bg-[#7C6AF7]/15 border border-[#7C6AF7]/30 flex items-center justify-center shrink-0 mt-0.5">
                        <span className="text-[9px] font-bold text-[#7C6AF7]">Q</span>
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-[#F0EEE9] leading-relaxed">{goal.goal_text}</p>
                        <p className="text-[10px] text-[#4A4A5A] mt-1 flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {safeFormatDistance(goal.created_at)}
                          {goal.intent && (
                            <span className="ml-2 px-1.5 py-0.5 rounded bg-[#2A2A32] text-[#7A7A8A] text-[9px] uppercase">
                              {goal.intent}
                            </span>
                          )}
                        </p>
                      </div>
                      <div className="shrink-0">
                        <GoalStatusChip status={goal.status} />
                      </div>
                    </div>

                    {/* Answer bubble */}
                    {goal.answer ? (
                      <div className="flex items-start gap-3 px-4 pb-4 border-t border-[#1A1A22]">
                        <div className="w-6 h-6 rounded-full bg-[#D4A853]/15 border border-[#D4A853]/30 flex items-center justify-center shrink-0 mt-3">
                          <span className="text-[9px] font-bold text-[#D4A853]">A</span>
                        </div>
                        <div className="flex-1 min-w-0 pt-3">
                          <div className="prose prose-sm prose-invert max-w-none text-[#F0EEE9]/85 text-sm leading-relaxed">
                            <ReactMarkdown>{goal.answer}</ReactMarkdown>
                          </div>
                        </div>
                      </div>
                    ) : goal.workflow_id ? (
                      <button
                        onClick={() => router.push(`/workspaces/${params.id}/workflows/${goal.workflow_id}`)}
                        className="w-full flex items-center gap-2 px-4 py-3 border-t border-[#1A1A22] text-xs text-[#7C6AF7] hover:text-[#9A8AFF] hover:bg-[#7C6AF7]/5 transition-colors text-left"
                      >
                        <ChevronRight className="w-3.5 h-3.5" />
                        View draft workflow →
                      </button>
                    ) : ["FAILED","ESCALATED","CANCELLED"].includes(goal.status) ? (
                      <div className="px-4 pb-3 border-t border-[#1A1A22]">
                        <p className="text-xs text-[#F06B6B] pt-3 flex items-center gap-1.5">
                          <AlertTriangle className="w-3.5 h-3.5" />
                          This request could not be completed.
                        </p>
                      </div>
                    ) : ["PENDING","PROCESSING"].includes(goal.status) ? (
                      <div className="px-4 pb-3 border-t border-[#1A1A22]">
                        <p className="text-xs text-[#7A7A8A] pt-3 flex items-center gap-1.5">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          Generating response...
                        </p>
                      </div>
                    ) : null}
                  </div>
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
                workspace.documents.map((doc) => {
                  const isReady = doc.status === "READY";
                  const isChecked = selectedDocIds.includes(doc.document_id);
                  return (
                    <div
                      key={doc.document_id}
                      className={`flex items-center justify-between px-4 py-3 hover:bg-[#1E1E28] transition-colors group ${
                        isReady && isChecked ? "bg-[#7C6AF7]/[0.02]" : ""
                      }`}
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        {isReady ? (
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => {
                              setSelectedDocIds((prev) =>
                                prev.includes(doc.document_id)
                                  ? prev.filter((id) => id !== doc.document_id)
                                  : [...prev, doc.document_id]
                              );
                            }}
                            className="w-3.5 h-3.5 rounded border-[#2A2A32] bg-[#16161D] text-[#7C6AF7] focus:ring-[#7C6AF7] focus:ring-offset-0 cursor-pointer"
                          />
                        ) : (
                          <FileText className="w-3.5 h-3.5 text-[#7A7A8A] shrink-0" strokeWidth={1.5} />
                        )}
                        <div className="min-w-0">
                          <p className="text-xs text-[#F0EEE9] truncate font-medium">{doc.filename}</p>
                          <p className="text-[10px] text-[#4A4A5A] mt-0.5">
                            {safeFormatDistance(doc.upload_date)}
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
                  );
                })
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
