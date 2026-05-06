// ── Shared TypeScript types ──────────────────────────────────────────────────

export interface User {
    sub: string;
    email: string;
    role: string;
    aud: string;
    org_id: string;
    org_slug: string;
    org_name: string;
    app_role: "ADMIN" | "MEMBER";
    personal_org_id?: string;
}

export interface OrgMember {
    user_id: string;
    email: string;
    full_name: string | null;
    role: "ADMIN" | "MEMBER";
    joined_at: string;
}

export interface OrgEntry {
    org_id: string;
    org_slug: string;
    org_name: string;
    role: "ADMIN" | "MEMBER";
    is_active: boolean;
}

export interface FileItem {
    file_id: number;
    filename: string;
    upload_date: string;
    status: "PENDING" | "PROCESSING" | "READY" | "FAILED";
}

export interface FileListResponse {
    total: number;
    limit: number;
    offset: number;
    files: FileItem[];
}

export interface UploadResult {
    filename: string;
    file_id?: number;
    status: "accepted" | "error" | "duplicate";
    message?: string;
    queue?: string;
}

export interface FileStatusResponse {
    file_id: number;
    filename: string;
    status: "PENDING" | "PROCESSING" | "READY" | "FAILED";
    error?: string;
}

export interface SessionFile {
    file_id: number;
    filename: string;
    status: string;
    progress?: number;
}

export interface SessionResponse {
    session_id: string;
    files: SessionFile[];
    ttl_hours: number;
    org_id: string;
}

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
}

export interface AskResponse {
    answer: string;
    sources?: string[];
}

export interface InviteVerifyResponse {
    email: string;
    org_name: string;
    org_id: string;
    role: string;
}

export interface SetupOrgResponse {
    message: string;
    org_id: string;
    org_slug: string;
    org_name: string;
    email: string;
    app_role: string;
}
export interface AcceptInviteResponse {
    message: string;
    user_id: string;
    org_id: string;
    access_token: string;
}

// ── Due Diligence (legaltech agentic pipeline) ────────────────────────────────

export interface PlanTask {
    task_id: number;
    task_type: string;
    description: string;
    search_target: string;
    reason: string;
}

export interface PlanResponse {
    goal: string;
    tasks: PlanTask[];
}

export interface CitationRef {
    document_name: string;
    clause_reference: string;
    exact_text: string;
}

export interface Escalation {
    trigger_name: string;
    severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    message: string;
    recommendation: string;
}

export interface DDFinding {
    task_id: string;
    task_description: string;
    analysis: string;
    risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    citations: CitationRef[];
    escalations: Escalation[];
}

export interface DefinitionalConflict {
    term: string;
    definitions: {
        term: string;
        definition: string;
        document_name: string;
        hierarchy_path: string;
    }[];
}

export interface DueDiligenceReport {
    goal: string;
    session_id?: string;
    generated_at: string;
    embedding_model_used: string;
    findings: DDFinding[];
    pre_ingestion_conflicts: DefinitionalConflict[];
    cross_document_contradictions: Escalation[];
    escalations: Escalation[];
    documents_analysed: {
        document_name: string;
        file_type: string;
        chunk_count: number;
        page_count: number;
    }[];
}

export interface IngestJobQueued {
    job_id: string;
    status: "queued";
    document_name: string;
}

export interface IngestJobStatus {
    job_id: string;
    status:
        | "queued"
        | "running"
        | "parsing"
        | "chunking"
        | "extracting_terms"
        | "parsing_references"
        | "building_graph"
        | "embedding"
        | "finalising"
        | "complete"
        | "failed";
    progress: number; // 0-100
    result?: IngestResult | null;
    error?: string | null;
}

export interface IngestResult {
    document: {
        document_name: string;
        file_type: string;
        chunk_count: number;
        page_count: number;
    };
    pre_ingestion_conflicts: DefinitionalConflict[];
    graph_nodes: number;
    graph_edges: number;
}

// ── Master Orchestrator (Action Agent) ───────────────────────────────────────

export type WorkflowStatus =
    | "CLASSIFYING"
    | "AWAITING_INTENT_CONFIRMATION"
    | "RETRIEVING"
    | "EXPANDING"
    | "REASONING"
    | "DRAFTING"
    | "AWAITING_APPROVAL"
    | "EXECUTING"
    | "REVISING"
    | "COMPLETED"
    | "FAILED";

export interface LexAction {
    id: string;
    action_type: string;
    description: string;
    status: string;
    urgency: number;
}

export interface LexToolCallLog {
    id: string;
    tool_name: string;
    status: string;
    summary: string;
    created_at: string;
}

export interface StartWorkflowResponse {
    workflow_id: string;
    status: WorkflowStatus;
    primary_intent?: string;
    intent_confidence?: number;
    findings_summary?: string;
}

export interface ConfirmIntentResponse {
    workflow_id: string;
    status: WorkflowStatus;
}

export interface ApproveWorkflowResponse {
    workflow_id: string;
    status: WorkflowStatus;
}

export interface WorkflowStatusResponse {
    workflow_id: string;
    status: WorkflowStatus;
    created_at: string;
    completed_at?: string;
}

export interface GetActionsResponse {
    actions: LexAction[];
}

export interface GetLogsResponse {
    logs: LexToolCallLog[];
}

// ── Workspace API Types ───────────────────────────────────────────────────────

export interface WorkspaceResponse {
    workspace_id: string;
    name: string;
    description?: string;
    intelligence_status: string;
    document_count: number;
    created_at: string;
    last_active_at?: string;
}

export interface WorkspaceDocument {
    document_id: string;
    filename: string;
    status: string;
    file_type?: string;
    file_hash?: string;
    stages?: Record<string, boolean> | null;
    error?: string | null;
    upload_date: string;
}

export interface WorkspaceDetailResponse extends WorkspaceResponse {
    documents: WorkspaceDocument[];
}

export interface WorkspaceSessionResponse {
    session_id: string;
    workspace_id: string;
    created_at: string;
    last_active_at: string;
    document_count: number;
    documents: WorkspaceDocument[];
    context?: Record<string, unknown> | null;
    prior_findings?: {
        claim: string;
        confidence: number;
        escalated: boolean;
        escalation_type?: string | null;
    }[];
}
