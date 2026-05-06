"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { getWorkspace, createWorkspaceSession } from "@/lib/api";
import { uploadFiles, listFiles, getFileStatus } from "@/lib/api";
import { startAgentWorkflow, getWorkflowStatus } from "@/lib/api";
import { WorkspaceDetailResponse, WorkspaceDocument } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { AmbiguityGate } from "@/components/workspace/AmbiguityGate";
import { AnalyzeResult } from "@/components/workspace/AnalyzeResult";
import { ReasonResult } from "@/components/workspace/ReasonResult";

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
    const [workspace, setWorkspace] = useState<WorkspaceDetailResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [uploading, setUploading] = useState(false);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [goalText, setGoalText] = useState("");
    const [goalCharCount, setGoalCharCount] = useState(0);
    const [processing, setProcessing] = useState(false);
    const [workflowId, setWorkflowId] = useState<string | null>(null);
    const [workflowStatus, setWorkflowStatus] = useState<string | null>(null);

    // Ambiguity gate state
    const [showAmbiguity, setShowAmbiguity] = useState(false);
    const [detectedIntent, setDetectedIntent] = useState<string>("");
    const [intentConfidence, setIntentConfidence] = useState(0);

    // Result state
    const [analyzeResult, setAnalyzeResult] = useState<any>(null);
    const [reasonResult, setReasonResult] = useState<any>(null);

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

    // Poll workflow status
    useEffect(() => {
        if (!workflowId || !token) return;
        const interval = setInterval(async () => {
            try {
                const res = await getWorkflowStatus(token, workflowId, orgSlug || undefined);
                setWorkflowStatus(res.status);

                if (res.status === "COMPLETED" || res.status === "FAILED") {
                    clearInterval(interval);
                    setProcessing(false);
                    // Reload workspace to refresh document statuses
                    loadWorkspace();
                }
            } catch {
                clearInterval(interval);
                setProcessing(false);
            }
        }, 2000);
        return () => clearInterval(interval);
    }, [workflowId, token, orgSlug, loadWorkspace]);

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files;
        if (!files?.length || !token) return;
        setUploading(true);
        try {
            await uploadFiles(token, Array.from(files), orgSlug || undefined);
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
        if (!token || !goalText.trim() || !sessionId || !workspace) return;
        setProcessing(true);
        setAnalyzeResult(null);
        setReasonResult(null);
        setShowAmbiguity(false);

        const firstDocId = workspace.documents.find((d) => d.status === "READY")?.document_id;
        if (!firstDocId) {
            setProcessing(false);
            return;
        }

        try {
            const res = await startAgentWorkflow(
                token,
                goalText.trim(),
                workspaceId,
                firstDocId,
                orgSlug || undefined,
            );
            setWorkflowId(res.workflow_id);
            setWorkflowStatus(res.status);

            if (res.primary_intent && res.intent_confidence !== undefined) {
                if (res.intent_confidence < 0.8) {
                    setDetectedIntent(res.primary_intent);
                    setIntentConfidence(res.intent_confidence);
                    setShowAmbiguity(true);
                } else {
                    setWorkflowStatus(res.status);
                }
            }
        } catch (err) {
            console.error("Workflow start failed:", err);
            setProcessing(false);
        }
    };

    const handleConfirmIntent = (confirmedIntent: string) => {
        setShowAmbiguity(false);
        setWorkflowStatus("EXECUTING");
    };

    const statusColor = (status: string) => {
        switch (status) {
            case "READY": return "green";
            case "PROCESSING": return "blue";
            case "PENDING": return "yellow";
            case "FAILED": return "red";
            default: return "slate";
        }
    };

    if (loading) {
        return (
            <div className="max-w-6xl mx-auto px-4 py-8">
                <div className="text-slate-400 text-center py-12">Loading workspace...</div>
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

    return (
        <div className="max-w-6xl mx-auto px-4 py-8">
            {/* Header */}
            <div className="mb-8">
                <h1 className="text-2xl font-bold text-white">{workspace.name}</h1>
                {workspace.description && (
                    <p className="text-slate-400 text-sm mt-1">{workspace.description}</p>
                )}
                <div className="flex items-center gap-3 mt-2">
                    <Badge color={statusColor(workspace.intelligence_status)}>
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
                        <Button as="span" disabled={uploading}>
                            {uploading ? "Uploading..." : "Upload PDF"}
                        </Button>
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
                    <p className="text-slate-500 text-sm py-4">No documents yet. Upload a PDF to get started.</p>
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
                                                <Badge key={stage} color={done ? "green" : "yellow"} size="sm">
                                                    {stage}
                                                </Badge>
                                            ))}
                                        </div>
                                    )}
                                </div>
                                <div className="flex items-center gap-2">
                                    <Badge color={statusColor(doc.status)} size="sm">
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
                        <Badge color="green">Session Active</Badge>
                        <span className="text-slate-400 text-xs ml-2 font-mono">{sessionId.slice(0, 8)}...</span>
                        <p className="text-slate-500 text-sm mt-2">{readyDocs.length} documents in context</p>
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
            {workflowStatus && workflowStatus !== "COMPLETED" && workflowStatus !== "FAILED" && (
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
        </div>
    );
}
