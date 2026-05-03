// ── legaltech Due Diligence API Client ──────────────────────────────────────
// Calls the legaltech agentic pipeline mounted at /api/v1/due-diligence.
// All authenticated endpoints require a Supabase JWT passed as Bearer token.

import type {
    IngestJobQueued,
    IngestJobStatus,
    IngestResult,
    PlanResponse,
    DueDiligenceReport,
    DefinitionalConflict,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
if (!API_URL && typeof window !== "undefined") {
    console.error("NEXT_PUBLIC_API_URL is not defined in legaltech-api.ts");
}
const DD_BASE = `${API_URL}/api/v1/due-diligence`;

function authHeaders(token?: string): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (token) h["Authorization"] = `Bearer ${token}`;
    return h;
}

// ── Due Diligence: Ingest (async — returns job_id, then poll) ────────────────

/**
 * Uploads a document and starts async ingestion.
 * Returns {job_id} immediately. Poll ddGetIngestStatus() until progress = 100.
 */
export async function ddIngestDocument(
    file: File,
    token?: string,
): Promise<IngestJobQueued> {
    const fd = new FormData();
    fd.append("file", file);

    const res = await fetch(`${DD_BASE}/ingest`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
    });

    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Ingest failed (${res.status}): ${res.statusText}`);
    }
    return res.json();
}

/**
 * Poll the ingest job status.
 * status: "queued" | "running" | "parsing" | "chunking" | "extracting_terms"
 *       | "parsing_references" | "building_graph" | "embedding" | "finalising"
 *       | "complete" | "failed"
 * progress: 0–100
 */
export async function ddGetIngestStatus(jobId: string): Promise<IngestJobStatus> {
    const res = await fetch(`${DD_BASE}/status/${jobId}`);
    if (!res.ok) throw new Error(`Status check failed: ${res.statusText}`);
    return res.json();
}

/**
 * Convenience: upload a file, then poll until complete or failed.
 * Calls onProgress(status, progress) on each poll tick.
 * Resolves to the final IngestResult on success.
 */
export async function ddIngestAndPoll(
    file: File,
    token?: string,
    onProgress?: (status: string, progress: number) => void,
    pollIntervalMs = 2000,
): Promise<IngestResult> {
    const { job_id } = await ddIngestDocument(file, token);

    return new Promise((resolve, reject) => {
        const timer = setInterval(async () => {
            try {
                const status = await ddGetIngestStatus(job_id);
                onProgress?.(status.status, status.progress);

                if (status.status === "complete" && status.result) {
                    clearInterval(timer);
                    resolve(status.result);
                } else if (status.status === "failed") {
                    clearInterval(timer);
                    reject(new Error(status.error || "Ingest failed"));
                }
            } catch (err) {
                clearInterval(timer);
                reject(err);
            }
        }, pollIntervalMs);
    });
}

// ── Due Diligence: Conflicts ──────────────────────────────────────────────────

export async function ddGetConflicts(token?: string): Promise<{
    conflict_count: number;
    conflicts: DefinitionalConflict[];
}> {
    const res = await fetch(`${DD_BASE}/conflicts`, {
        headers: authHeaders(token),
    });
    if (!res.ok) throw new Error(`Conflicts fetch failed: ${res.statusText}`);
    return res.json();
}

// ── Due Diligence: Plan ───────────────────────────────────────────────────────

export async function ddPlanGoal(
    goal: string,
    token?: string,
): Promise<PlanResponse> {
    const res = await fetch(`${DD_BASE}/plan`, {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ goal }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Plan failed: ${res.statusText}`);
    }
    return res.json();
}

// ── Due Diligence: Execute ────────────────────────────────────────────────────

export async function ddExecutePlan(
    goal: string,
    tasks: PlanResponse["tasks"],
    documentNames: string[],
    token?: string,
    timeoutMs = 180_000,
): Promise<DueDiligenceReport> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const res = await fetch(`${DD_BASE}/execute`, {
            method: "POST",
            headers: authHeaders(token),
            body: JSON.stringify({ goal, tasks, document_names: documentNames }),
            signal: controller.signal,
        });
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body?.detail || `Execute failed: ${res.statusText}`);
        }
        return res.json();
    } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") {
            throw new Error("Due diligence execution timed out (> 3 min). The server is still working — refresh to check for results.");
        }
        throw err;
    } finally {
        clearTimeout(timeout);
    }
}

// ── Master Orchestrator (Action Agent) ────────────────────────────────────────

import type {
    StartWorkflowResponse,
    ConfirmIntentResponse,
    ApproveWorkflowResponse,
    WorkflowStatusResponse,
    GetActionsResponse,
    GetLogsResponse
} from "./types";

const AGENT_BASE = `${API_URL}/agent`;

export async function lexAgentStart(
    goal_id: string,
    workspace_id: string,
    org_id: string,
    document_id: string,
    token?: string
): Promise<StartWorkflowResponse> {
    const res = await fetch(`${AGENT_BASE}/start`, {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ goal_id, workspace_id, org_id, document_id }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Agent start failed: ${res.statusText}`);
    }
    return res.json();
}

export async function lexAgentConfirmIntent(
    workflow_id: string,
    confirmed_intent: string,
    token?: string
): Promise<ConfirmIntentResponse> {
    const res = await fetch(`${AGENT_BASE}/confirm-intent/${workflow_id}`, {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify({ confirmed_intent }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Agent confirm intent failed: ${res.statusText}`);
    }
    return res.json();
}

export async function lexAgentApprove(
    workflow_id: string,
    token?: string
): Promise<ApproveWorkflowResponse> {
    const res = await fetch(`${AGENT_BASE}/approve/${workflow_id}`, {
        method: "POST",
        headers: authHeaders(token),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Agent approve failed: ${res.statusText}`);
    }
    return res.json();
}

export async function lexAgentStatus(
    workflow_id: string,
    token?: string
): Promise<WorkflowStatusResponse> {
    const res = await fetch(`${AGENT_BASE}/status/${workflow_id}`, {
        headers: authHeaders(token),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Agent status failed: ${res.statusText}`);
    }
    return res.json();
}

export async function lexAgentGetActions(
    workflow_id: string,
    token?: string
): Promise<GetActionsResponse> {
    const res = await fetch(`${AGENT_BASE}/${workflow_id}/actions`, {
        headers: authHeaders(token),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Agent actions failed: ${res.statusText}`);
    }
    return res.json();
}

export async function lexAgentGetLogs(
    workflow_id: string,
    token?: string
): Promise<GetLogsResponse> {
    const res = await fetch(`${AGENT_BASE}/${workflow_id}/logs`, {
        headers: authHeaders(token),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Agent logs failed: ${res.statusText}`);
    }
    return res.json();
}
