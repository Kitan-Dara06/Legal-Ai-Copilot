"use client";
export const dynamic = "force-dynamic";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import {
    Upload, FileText, X, CheckCircle, Loader2,
    AlertTriangle, Scale, Sparkles, ChevronRight,
} from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { PlanReview } from "@/components/due-diligence/PlanReview";
import { ReportView } from "@/components/due-diligence/ReportView";
import { ConflictsBanner } from "@/components/due-diligence/ConflictsBanner";
import { AmbiguityGateModal } from "@/components/due-diligence/AmbiguityGateModal";
import { 
    ddIngestAndPoll, 
    lexAgentStart, 
    lexAgentConfirmIntent, 
    lexAgentApprove, 
    lexAgentStatus, 
    lexAgentGetActions, 
    lexAgentGetLogs 
} from "@/lib/legaltech-api";
import type {
    IngestResult, DefinitionalConflict, WorkflowStatus, LexAction, LexToolCallLog, StartWorkflowResponse
} from "@/lib/types";

// ── Types ────────────────────────────────────────────────────────────────────

type Step = "upload" | "goal" | "agent";

interface UploadedDoc {
    name: string;
    result: IngestResult;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function stepNum(s: Step): number {
    return { upload: 1, goal: 2, agent: 3 }[s];
}

function StepIndicator({ current }: { current: Step }) {
    const steps: { key: Step; label: string }[] = [
        { key: "upload", label: "Upload" },
        { key: "goal",   label: "Goal" },
        { key: "agent",  label: "Agent" },
    ];
    const cur = stepNum(current);

    return (
        <div className="flex items-center gap-0">
            {steps.map((s, idx) => {
                const n = idx + 1;
                const done    = n < cur;
                const active  = n === cur;
                return (
                    <div key={s.key} className="flex items-center">
                        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
                            active  ? "bg-accent-blue/20 text-accent-blue border border-accent-blue/30" :
                            done    ? "text-emerald-400"  : "text-slate-600"
                        }`}>
                            {done ? (
                                <CheckCircle className="w-3.5 h-3.5" />
                            ) : (
                                <span className={`w-4 h-4 rounded-full border flex items-center justify-center text-[9px] ${
                                    active ? "border-accent-blue text-accent-blue" : "border-slate-700 text-slate-600"
                                }`}>{n}</span>
                            )}
                            {s.label}
                        </div>
                        {idx < steps.length - 1 && (
                            <ChevronRight className="w-3 h-3 text-slate-800 mx-0.5" />
                        )}
                    </div>
                );
            })}
        </div>
    );
}

// ── Upload Panel ─────────────────────────────────────────────────────────────

function UploadPanel({
    docs, conflicts, onIngest, onDismissConflicts, onProceed,
}: {
    docs: UploadedDoc[];
    conflicts: DefinitionalConflict[];
    onIngest: (file: File) => Promise<void>;
    onDismissConflicts: () => void;
    onProceed: () => void;
}) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [uploading, setUploading] = useState(false);
    const [progress, setProgress] = useState(0);
    const [dragOver, setDragOver] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleFiles = useCallback(async (files: FileList | null) => {
        if (!files || files.length === 0) return;
        setError(null);
        setUploading(true);
        setProgress(0);
        try {
            for (const file of Array.from(files)) {
                await onIngest(file);
            }
        } catch (e: any) {
            setError(e.message || "Upload failed");
        } finally {
            setUploading(false);
            setProgress(0);
        }
    }, [onIngest]);

    return (
        <div className="flex flex-col gap-5 max-w-2xl mx-auto">
            <ConflictsBanner conflicts={conflicts} onDismiss={onDismissConflicts} />

            <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
                onClick={() => inputRef.current?.click()}
                className={`relative border-2 border-dashed rounded-xl px-6 py-10 flex flex-col items-center justify-center cursor-pointer transition-all ${
                    dragOver
                        ? "border-accent-blue/60 bg-accent-blue/5 scale-[1.01]"
                        : "border-slate-700 hover:border-slate-600 bg-slate-900/40 hover:bg-slate-900/60"
                }`}
            >
                <input
                    ref={inputRef}
                    type="file"
                    accept=".pdf,.docx"
                    multiple
                    className="hidden"
                    onChange={(e) => handleFiles(e.target.files)}
                />

                {uploading ? (
                    <>
                        <Loader2 className="w-8 h-8 text-accent-blue animate-spin mb-3" />
                        <p className="text-sm text-slate-300 font-medium">Ingesting document…</p>
                        <div className="w-48 mt-3 h-1 bg-slate-800 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-accent-blue transition-all duration-300"
                                style={{ width: `${progress}%` }}
                            />
                        </div>
                    </>
                ) : (
                    <>
                        <div className="w-12 h-12 rounded-full bg-accent-blue/10 border border-accent-blue/20 flex items-center justify-center mb-3">
                            <Upload className="w-5 h-5 text-accent-blue" />
                        </div>
                        <p className="text-sm text-slate-300 font-medium">Drop contracts here</p>
                        <p className="text-xs text-slate-600 mt-1">PDF or DOCX · Click to browse</p>
                    </>
                )}
            </div>

            {error && (
                <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                    <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
                    <p className="text-xs text-red-300">{error}</p>
                </div>
            )}

            {docs.length > 0 && (
                <div className="space-y-2">
                    <p className="text-[10px] text-slate-500 uppercase tracking-widest">
                        Indexed ({docs.length})
                    </p>
                    {docs.map((doc) => (
                        <div key={doc.name} className="flex items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-2.5">
                            <FileText className="w-4 h-4 text-accent-blue flex-shrink-0" />
                            <div className="flex-1 min-w-0">
                                <p className="text-xs text-slate-300 truncate font-medium">{doc.name}</p>
                                <p className="text-[10px] text-slate-600 mt-0.5">
                                    {doc.result.document.chunk_count} chunks · {doc.result.graph_nodes} graph nodes
                                </p>
                            </div>
                            <CheckCircle className="w-4 h-4 text-emerald-400 flex-shrink-0" />
                        </div>
                    ))}
                </div>
            )}

            {docs.length > 0 && (
                <button
                    onClick={onProceed}
                    className="w-full flex items-center justify-center gap-2 py-3 bg-accent-blue hover:bg-blue-500 text-white text-sm font-semibold rounded-xl transition-all shadow-lg shadow-blue-500/20"
                >
                    <Sparkles className="w-4 h-4" />
                    Define Your Goal →
                </button>
            )}
        </div>
    );
}

// ── Goal Panel ────────────────────────────────────────────────────────────────

function GoalPanel({
    docs, onSubmit, onBack, isSubmitting,
}: {
    docs: UploadedDoc[];
    onSubmit: (goal: string) => void;
    onBack: () => void;
    isSubmitting: boolean;
}) {
    const [goal, setGoal] = useState("");

    const examples = [
        "Review all deadline obligations and send required notices.",
        "Compare the indemnification clauses to standard company policy.",
        "Generate a brief summary of the termination conditions.",
    ];

    return (
        <div className="flex flex-col gap-5 max-w-2xl mx-auto">
            <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
                <p className="text-[10px] text-slate-500 uppercase tracking-widest mb-2">Documents in scope</p>
                <div className="flex flex-wrap gap-1.5">
                    {docs.map((d) => (
                        <span key={d.name} className="text-[10px] text-slate-400 bg-slate-800 px-2 py-0.5 rounded font-mono max-w-[200px] truncate">
                            {d.name}
                        </span>
                    ))}
                </div>
            </div>

            <div className="space-y-2">
                <label className="text-xs text-slate-400 font-medium">What do you want to accomplish?</label>
                <textarea
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    placeholder="Describe what you need the agent to do…"
                    rows={4}
                    className="w-full bg-slate-900 border border-slate-700 focus:border-accent-blue/60 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder-slate-600 outline-none resize-none transition-colors"
                />
            </div>

            <div className="space-y-2">
                <p className="text-[10px] text-slate-600 uppercase tracking-widest">Example goals</p>
                {examples.map((ex) => (
                    <button
                        key={ex}
                        onClick={() => setGoal(ex)}
                        className="w-full text-left text-xs text-slate-500 hover:text-slate-300 bg-slate-900/40 hover:bg-slate-900 border border-slate-800 hover:border-slate-700 rounded-lg px-3 py-2.5 transition-all"
                    >
                        {ex}
                    </button>
                ))}
            </div>

            <div className="flex gap-3 pt-2">
                <button
                    onClick={onBack}
                    disabled={isSubmitting}
                    className="px-4 py-2 text-sm text-slate-400 hover:text-white border border-slate-700 hover:border-slate-600 rounded-lg transition-colors disabled:opacity-40"
                >
                    ← Back
                </button>
                <button
                    onClick={() => goal.trim() && onSubmit(goal.trim())}
                    disabled={!goal.trim() || isSubmitting}
                    className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-accent-blue hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all shadow-lg shadow-blue-500/20"
                >
                    {isSubmitting ? (
                        <><Loader2 className="w-4 h-4 animate-spin" /> Starting Agent…</>
                    ) : (
                        <><Sparkles className="w-4 h-4" /> Start Lex Agent</>
                    )}
                </button>
            </div>
        </div>
    );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DueDiligencePage() {
    const router = useRouter();
    const [step, setStep]         = useState<Step>("upload");
    const [docs, setDocs]         = useState<UploadedDoc[]>([]);
    const [conflicts, setConflicts] = useState<DefinitionalConflict[]>([]);
    const [showConflicts, setShowConflicts] = useState(true);

    // Agent State
    const [workflowId, setWorkflowId] = useState<string | null>(null);
    const [agentStatus, setAgentStatus] = useState<WorkflowStatus | null>(null);
    const [startResponse, setStartResponse] = useState<StartWorkflowResponse | null>(null);
    const [actions, setActions] = useState<LexAction[]>([]);
    const [logs, setLogs] = useState<LexToolCallLog[]>([]);
    
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [goalText, setGoalText] = useState("");

    // ── Auth guard ──────────────────────────────────────────────────────────
    const supabase = createClient();
    const [token, setToken] = useState<string | undefined>(undefined);
    const [workspaceId, setWorkspaceId] = useState<string>("7c2dd358-3ff3-4d46-8889-27a704b7cc4c"); // default mock
    const [orgId, setOrgId] = useState<string>("b2b3cfdf-8b57-4a8c-aecd-f3567ff67689"); // default mock

    useEffect(() => {
        supabase.auth.getSession().then(({ data }) => {
            if (!data.session) {
                router.replace("/login");
                return;
            }
            setToken(data.session.access_token);
        });
    }, [supabase, router]);

    // Polling Loop for Agent Status
    useEffect(() => {
        if (!workflowId || !token) return;
        
        let isPolling = true;
        const poll = async () => {
            while (isPolling) {
                try {
                    const res = await lexAgentStatus(workflowId, token);
                    setAgentStatus(res.status);
                    
                    if (res.status === "AWAITING_APPROVAL") {
                        const actRes = await lexAgentGetActions(workflowId, token);
                        setActions(actRes.actions);
                        isPolling = false;
                    } else if (res.status === "COMPLETED" || res.status === "FAILED") {
                        const logRes = await lexAgentGetLogs(workflowId, token);
                        setLogs(logRes.logs);
                        isPolling = false;
                    } else if (res.status === "AWAITING_INTENT_CONFIRMATION") {
                        isPolling = false;
                    }
                } catch (e) {
                    console.error("Polling error", e);
                }
                
                if (isPolling) await new Promise((r) => setTimeout(r, 3000));
            }
        };
        poll();
        return () => { isPolling = false; };
    }, [workflowId, token]);

    // ── Upload handler (async poll) ─────────────────────────────────────────
    const handleIngest = useCallback(async (file: File) => {
        const result = await ddIngestAndPoll(file, token);
        setDocs((prev) => {
            const filtered = prev.filter((d) => d.name !== file.name);
            return [...filtered, { name: file.name, result }];
        });
        if (result.pre_ingestion_conflicts?.length > 0) {
            setConflicts(result.pre_ingestion_conflicts);
            setShowConflicts(true);
        }
    }, [token]);

    // ── Start Agent handler ──────────────────────────────────────────────────
    const handleStartAgent = useCallback(async (goal: string) => {
        setIsSubmitting(true);
        setError(null);
        setGoalText(goal);
        try {
            // Create a Goal record in Postgres directly using supabase or mock for now.
            // Since we don't have a POST /goal endpoint built yet, we'll assume the API creates it or we use a hardcoded goal id for the test workspace
            // We use a predefined goal_id that exists in our db from our E2E test
            const mockGoalId = "4eca8f09-de26-4c78-a2cc-ac86b3f1889e"; 
            const mockDocId = "b00a2b59-922c-490d-a3b9-5883813384af"; // mock

            const res = await lexAgentStart(mockGoalId, workspaceId, orgId, mockDocId, token);
            setStartResponse(res);
            setWorkflowId(res.workflow_id);
            setAgentStatus(res.status);
            setStep("agent");
        } catch (e: any) {
            setError(e.message || "Failed to start agent");
        } finally {
            setIsSubmitting(false);
        }
    }, [token, workspaceId, orgId]);

    // ── Confirm Intent handler ───────────────────────────────────────────────
    const handleConfirmIntent = async (intent: string) => {
        if (!workflowId) return;
        try {
            const res = await lexAgentConfirmIntent(workflowId, intent, token);
            setAgentStatus(res.status);
        } catch (e: any) {
            setError(e.message);
        }
    };

    // ── Approve handler ──────────────────────────────────────────────────────
    const handleApprove = async () => {
        if (!workflowId) return;
        try {
            const res = await lexAgentApprove(workflowId, token);
            setAgentStatus(res.status);
        } catch (e: any) {
            setError(e.message);
        }
    };

    const handleReset = () => {
        setStep("upload");
        setDocs([]);
        setConflicts([]);
        setWorkflowId(null);
        setAgentStatus(null);
        setStartResponse(null);
        setActions([]);
        setLogs([]);
        setError(null);
    };

    return (
        <div className="flex h-screen bg-navy-950 overflow-hidden flex-col">
            <TopBar sessionActive={false} mode="due-diligence" />

            {/* Main scroll area */}
            <div className="flex-1 overflow-y-auto">
                <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">

                    {/* Page heading */}
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="w-8 h-8 rounded-lg bg-accent-blue/10 border border-accent-blue/20 flex items-center justify-center">
                                <Scale className="w-4 h-4 text-accent-blue" />
                            </div>
                            <div>
                                <h1 className="text-base font-semibold text-white">Lex Workspace</h1>
                                <p className="text-[11px] text-slate-500">Unified Agentic Execution</p>
                            </div>
                        </div>
                        <StepIndicator current={step} />
                    </div>

                    {/* Global error */}
                    {error && (
                        <div className="flex items-start gap-3 bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
                            <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
                            <div className="flex-1">
                                <p className="text-xs text-red-300">{error}</p>
                            </div>
                            <button onClick={() => setError(null)}><X className="w-3.5 h-3.5 text-red-400/50 hover:text-red-400" /></button>
                        </div>
                    )}

                    {/* Modals */}
                    {agentStatus === "AWAITING_INTENT_CONFIRMATION" && startResponse && (
                        <AmbiguityGateModal 
                            guessedIntent={startResponse.primary_intent || "ANALYZE"} 
                            confidence={startResponse.intent_confidence || 0}
                            onConfirm={handleConfirmIntent}
                        />
                    )}

                    {/* Step panels */}
                    {step === "upload" && (
                        <UploadPanel
                            docs={docs}
                            conflicts={showConflicts ? conflicts : []}
                            onIngest={handleIngest}
                            onDismissConflicts={() => setShowConflicts(false)}
                            onProceed={() => setStep("goal")}
                        />
                    )}

                    {step === "goal" && (
                        <GoalPanel
                            docs={docs}
                            onSubmit={handleStartAgent}
                            onBack={() => setStep("upload")}
                            isSubmitting={isSubmitting}
                        />
                    )}

                    {step === "agent" && (
                        <div className="flex flex-col items-center pt-8">
                            {/* Loading State */}
                            {["CLASSIFYING", "RETRIEVING", "EXPANDING", "REASONING", "DRAFTING", "EXECUTING"].includes(agentStatus || "") && (
                                <div className="text-center py-20">
                                    <Loader2 className="w-10 h-10 text-accent-blue animate-spin mx-auto mb-4" />
                                    <h3 className="text-white text-lg font-semibold mb-1">
                                        {agentStatus === "CLASSIFYING" ? "Analyzing your goal..." : 
                                         agentStatus === "EXECUTING" ? "Executing task plan..." : 
                                         "Processing..."}
                                    </h3>
                                    <p className="text-slate-400 text-sm">The agent is working on your request.</p>
                                </div>
                            )}

                            {/* Plan Review State */}
                            {agentStatus === "AWAITING_APPROVAL" && workflowId && (
                                <PlanReview 
                                    workflowId={workflowId}
                                    actions={actions}
                                    onConfirm={handleApprove}
                                    isExecuting={false}
                                />
                            )}

                            {/* Final Output State */}
                            {(agentStatus === "COMPLETED" || agentStatus === "FAILED") && (
                                <ReportView 
                                    goal={goalText}
                                    findingsSummary={startResponse?.findings_summary || "Agent finished execution."}
                                    logs={logs}
                                    onReset={handleReset}
                                />
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
