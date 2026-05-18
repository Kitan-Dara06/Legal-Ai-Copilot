"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { getWorkspace, createWorkspaceSession } from "@/lib/api";
import { uploadDocument } from "@/lib/api";
import { createGoal, getGoalStatus, listGoals } from "@/lib/api";
import { WorkspaceDetailResponse, WorkspaceDocument } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { AmbiguityGate } from "@/components/workspace/AmbiguityGate";
import { AnalyzeResult } from "@/components/workspace/AnalyzeResult";
import { ReasonResult } from "@/components/workspace/ReasonResult";
import { ActResult } from "@/components/workspace/ActResult";

const STATUS_PROGRESS: Record<string, number> = {
  PENDING: 0.2,
  PROCESSING: 0.6,
  READY: 1.0,
  FAILED: 0.0,
};

export default function WorkspaceDetailPage() {
  const params = useParams();
  const workspaceId = params.id as string;

  const [token, setToken] = useState<string | null>(null);
  const [orgSlug, setOrgSlug] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceDetailResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [goalText, setGoalText] = useState("");
  const [goalCharCount, setGoalCharCount] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<string | null>(null);
  const [goalId, setGoalId] = useState<string | null>(null);

  // Ambiguity gate state
  const [showAmbiguity, setShowAmbiguity] = useState(false);
  const [detectedIntent, setDetectedIntent] = useState<string>("");
  const [intentConfidence, setIntentConfidence] = useState(0);

  // Result state
  const [analyzeResult, setAnalyzeResult] = useState<any>(null);
  const [reasonResult, setReasonResult] = useState<any>(null);
  const [actResult, setActResult] = useState<any>(null);

  useEffect(() => {
    const stored = localStorage.getItem("sb-access-token");
    const org = localStorage.getItem("sb-org-slug");
    if (stored) setToken(stored);
    if (org) setOrgSlug(org);
  }, []);

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

  // Poll goal status
  useEffect(() => {
    if (!goalId || !token) return;
    const interval = setInterval(async () => {
      try {
        const res = await getGoalStatus(
          token,
          workspaceId,
          goalId,
          orgSlug || undefined,
        );
        setWorkflowStatus(res.status);

        const workflows = (res as any).workflows as any[] | undefined;
        if (res.status === "AWAITING_APPROVAL" && workflows && workflows.length > 0) {
          const wfId = workflows[0].id;
          try {
            const actData = await getWorkflowActions(token, wfId, orgSlug || undefined);
            setActResult({ actions: actData.actions, workflow_id: wfId });
          } catch (e) {
            console.error("Failed to fetch actions:", e);
          }
        }

        if (res.status === "COMPLETED" || res.status === "FAILED") {
          clearInterval(interval);
          setProcessing(false);
          loadWorkspace();
        }
      } catch {
        clearInterval(interval);
        setProcessing(false);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [goalId, token, workspaceId, orgSlug, loadWorkspace, getWorkflowActions]);

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

    try {
      const res = await createGoal(
        token,
        workspaceId,
        goalText.trim(),
        undefined,
        orgSlug || undefined,
      );

      if (res.goal_id) {
        setGoalId(res.goal_id);
      }

      if (res.primary_intent && res.intent_confidence !== undefined) {
        if (res.intent_confidence < 0.8 && res.primary_intent) {
          setDetectedIntent(res.primary_intent);
          setIntentConfidence(res.intent_confidence);
          setShowAmbiguity(true);
        }
      }

      if (res.workflow_id) {
        setWorkflowId(res.workflow_id);
        setWorkflowStatus(res.status || "PROCESSING");
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
      console.error("Goal creation failed:", err);
      setProcessing(false);
    }
  };

  const handleConfirmIntent = (confirmedIntent: string) => {
    setShowAmbiguity(false);
    setWorkflowStatus("EXECUTING");
  };

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

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="text-slate-400 text-center py-12">
          Loading workspace...
        </div>
      </div>
    );
  }

  if (!workspace) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="text-slate-400 text-center py-12">
          Workspace not found
        </div>
      </div>
    );
  }

  const readyDocs = workspace.documents.filter((d) => d.status === "READY");

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
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

      {/* Document Library */}
      <Card className="mb-6 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Document Library</h2>
          <label className="cursor-pointer">
            <span
              className={`inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors px-4 py-2 ${
                uploading
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
                  <Badge variant={statusVariant(doc.status)}>
                    {doc.status}
                  </Badge>
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
          <h2 className="text-lg font-semibold text-white mb-4">
            What do you need?
          </h2>
          <div className="relative">
            <textarea
              className="w-full bg-slate-800 border border-slate-700 rounded-lg p-4 text-white placeholder-slate-500 resize-none focus:outline-none focus:border-blue-500"
              rows={3}
              placeholder="What do you need?"
              value={goalText}
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
            <Button
              onClick={handleSubmitGoal}
              disabled={!goalText.trim() || processing}
            >
              {processing ? "Processing..." : "Submit"}
            </Button>
          </div>
        </Card>
      )}

      {/* Workflow Status */}
      {workflowStatus &&
        workflowStatus !== "COMPLETED" &&
        workflowStatus !== "FAILED" && (
          <Card className="mb-6 p-4">
            <div className="flex items-center gap-3">
              <div className="animate-spin h-4 w-4 border-2 border-blue-500 border-t-transparent rounded-full" />
              <span className="text-slate-300 text-sm">{workflowStatus}</span>
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
        <ActResult
          actions={actResult.actions}
          workflowId={actResult.workflow_id}
        />
      )}
    </div>
  );
}
