// ── Shared TypeScript types ───────────────────────────────────────────────────

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

// ── Document & Workspace ──────────────────────────────────────────────────────

export interface WorkspaceDocument {
  document_id: string;
  filename: string;
  status: "PENDING" | "PROCESSING" | "READY" | "FAILED";
  file_type?: string;
  file_hash?: string;
  stages?: Record<string, boolean> | null;
  error?: string | null;
  upload_date: string;
}

export interface WorkspaceResponse {
  workspace_id: string;
  name: string;
  description?: string;
  intelligence_status: string;
  document_count: number;
  created_at: string;
  last_active_at?: string;
}

export interface WorkspaceDetailResponse extends WorkspaceResponse {
  documents: WorkspaceDocument[];
}

// ── Goals ─────────────────────────────────────────────────────────────────────

export type GoalIntent = "ANALYZE" | "REASON" | "ACT";

export interface GoalSummary {
  id: string;
  goal_text: string;
  status: string;
  intent?: GoalIntent | null;
  workflow_id?: string | null;
  answer?: string | null;
  created_at: string;
}

export interface GoalDetail extends GoalSummary {
  mode?: string | null;
  workflows?: WorkflowStatusResponse[];
}

export interface CreateGoalResponse {
  goal_id: string;
  status: string;
  answer?: string | null;
  workflow_id?: string | null;
  intent?: GoalIntent;           // backend sends "intent", not "primary_intent"
  intent_confidence?: number;
}

export interface ConfirmIntentResponse {
  goal_id: string;
  status: string;
}

// ── Workflow Status ───────────────────────────────────────────────────────────

export type WorkflowStatus =
  | "CLASSIFYING"
  | "AWAITING_INTENT_CONFIRMATION"
  | "RETRIEVING"
  | "EXPANDING"
  | "REASONING"
  | "BRIEFING"                       // NEW: decision_brief_node running
  | "AWAITING_BRIEF_CONFIRMATION"    // NEW: HITL pause 1
  | "DRAFTING"
  | "AWAITING_APPROVAL"              // HITL pause 2
  | "REVISING"
  | "RECOVERING"
  | "COMPLETED"
  | "ESCALATED"
  | "CANCELLED"
  | "FAILED";

export interface WorkflowStatusResponse {
  workflow_id: string;
  status: WorkflowStatus;
  created_at: string;
  completed_at?: string | null;
}

// ── Decision Brief (ACT Phase 1) ──────────────────────────────────────────────

export interface BriefClause {
  clause_ref: string;
  excerpt: string;
  relevance_reason: string;
  document_id: string;
}

export interface BriefConflict {
  type: string;
  description: string;
  source_a: string;
  source_b?: string | null;
}

export interface BriefAction {
  action_type: "DRAFT_RESPONSE" | "DRAFT_NOTICE" | "DRAFT_AMENDMENT";
  description: string;
  urgency: number;
  deadline_date?: string | null;
}

export interface DecisionBrief {
  goal: string;
  summary: string;
  recommended_actions: BriefAction[];
  relevant_clauses: BriefClause[];
  conflicts_to_resolve: BriefConflict[];
  deadlines_implicated: string[];
  verification_checklist: string[];
  proceed_recommended: boolean;
  proceed_reasoning: string;
  confidence: number;
}

export interface WorkflowBriefResponse {
  workflow_id: string;
  status: WorkflowStatus;
  brief: DecisionBrief;
}

// ── Draft (ACT Phase 2) ───────────────────────────────────────────────────────

export interface DraftPayload {
  draft_text: string;
  source_citations: string[];
  grounding_score: number;
  missing_info: string[];
  verification_checklist: string[];
}

export interface LexAction {
  id: string;
  action_type: string;
  description: string;
  status: string;
  urgency: number;
  draft_payload?: DraftPayload | null;
}

export interface GetActionsResponse {
  actions: LexAction[];
}

// ── Ingestion ─────────────────────────────────────────────────────────────────

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
    | "queued" | "running" | "parsing" | "chunking"
    | "extracting_terms" | "parsing_references"
    | "building_graph" | "embedding" | "finalising"
    | "complete" | "failed";
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

// ── Deadlines ─────────────────────────────────────────────────────────────────

export interface DeadlineItem {
  id: string;
  obligation_description: string;
  obligation_type: string;
  raw_date_expression: string;
  resolved_deadline: string | null;
  resolution_status: string;
  conflict_flag: boolean;
  status: string;
  urgency_score: number;
  days_remaining?: number;
}

// ── Notifications ─────────────────────────────────────────────────────────────

export interface NotificationItem {
  id: string;
  title: string;
  body: string;
  notification_type: string;
  action_url?: string | null;
  is_read: boolean;
  created_at: string;
}

// ── Escalations ───────────────────────────────────────────────────────────────

export interface EscalationItem {
  id: string;
  status: string;
  intent: string;
  error_context?: string;
  created_at: string;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

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

// ── Legacy compat (used by old sidebar components) ────────────────────────────
export interface FileItem {
  file_id: number;
  document_id?: string;
  filename: string;
  upload_date: string;
  status: "PENDING" | "PROCESSING" | "READY" | "FAILED";
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface DependencyInfo {
  action_id: string;
  depends_on: string[];
}
