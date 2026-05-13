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
  document_id?: string; // UUID from the new document system
  filename: string;
  upload_date: string;
  status: "PENDING" | "PROCESSING" | "READY" | "FAILED";
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
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

// ── Due Diligence Pipeline ────────────────────────────────────────────────────

export interface DefinitionalConflict {
  term: string;
  definitions: {
    term: string;
    definition: string;
    document_name: string;
    hierarchy_path: string;
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
  progress: number;
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

// ── Unified Goals API Types ─────────────────────────────────────────────────

export type GoalIntent = "ANALYZE" | "REASON" | "ACT";

export interface CreateGoalResponse {
  goal_id: string;
  status: string;
  answer?: string | null;
  workflow_id?: string | null;
  primary_intent?: GoalIntent;
  intent_confidence?: number;
}

export interface GoalSummary {
  id: string;
  goal_text: string;
  status: string;
  intent?: GoalIntent | null;
  mode?: string | null;
  answer?: string | null;
  created_at: string;
}

export interface GoalDetail {
  id: string;
  goal_text: string;
  status: string;
  intent?: GoalIntent | null;
  mode?: string | null;
  answer?: string | null;
  created_at: string;
  workflows?: any[];
}

export interface ConfirmIntentResponse {
  goal_id: string;
  status: string;
}

// ── Approval API Types ──────────────────────────────────────────────────────

export interface ApprovalRequest {
  id: string;
  workflow_id: string;
  status: string;
  action_type: string;
  description: string;
  urgency_score: number;
  token_hash?: string;
  expires_at: string;
  created_at: string;
}

// ── Notification API Types ──────────────────────────────────────────────────

export interface NotificationItem {
  id: string;
  title: string;
  body: string;
  notification_type: string;
  action_url?: string | null;
  is_read: boolean;
  created_at: string;
}

// ── Act Path Types ──────────────────────────────────────────────────────────

export interface DependencyInfo {
  action_id: string;
  depends_on: string[];
}
