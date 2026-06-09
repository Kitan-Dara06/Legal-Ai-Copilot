// ── API Service Layer ─────────────────────────────────────────────────────────
// Typed wrapper around fetch. All calls require a Supabase JWT token.

import type {
  AcceptInviteResponse,
  ConfirmIntentResponse,
  CreateGoalResponse,
  DeadlineItem,
  DecisionBrief,
  EscalationItem,
  GetActionsResponse,
  GoalDetail,
  GoalIntent,
  GoalSummary,
  IngestJobQueued,
  IngestJobStatus,
  InviteVerifyResponse,
  LexAction,
  NotificationItem,
  OrgEntry,
  OrgMember,
  SetupOrgResponse,
  User,
  WorkflowBriefResponse,
  WorkflowStatusResponse,
  WorkspaceDetailResponse,
  WorkspaceResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

if (!API_URL && typeof window !== "undefined") {
  console.error("NEXT_PUBLIC_API_URL is not defined. API calls will fail.");
}

// ── Helpers ───────────────────────────────────────────────────────────────────

type Headers = Record<string, string>;

export class AppError extends Error {
  code?: string;
  status: number;
  constructor(message: string, code: string | undefined, status: number) {
    super(message);
    this.code = code;
    this.status = status;
    this.name = "AppError";
  }
}

function buildHeaders(token?: string | null, orgSlug?: string | null): Headers {
  const h: Headers = {};
  if (token) h["Authorization"] = `Bearer ${token}`;
  if (orgSlug) h["X-Active-Org"] = orgSlug;
  return h;
}

export async function apiFetch<T>(
  path: string,
  opts: RequestInit & { token?: string | null; orgSlug?: string | null } = {},
): Promise<T> {
  const { token, orgSlug, headers: extraHeaders, ...rest } = opts;
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    ...rest,
    headers: { ...buildHeaders(token, orgSlug), ...(extraHeaders as Headers) },
  });
  if (!res.ok) {
    // 401 — session expired. Throw a typed error so callers can redirect to login.
    if (res.status === 401 || res.status === 403) {
      throw new AppError("AUTH_EXPIRED", "AUTH_EXPIRED", res.status);
    }
    // 5xx — server crash. Never expose raw internal detail to the UI.
    if (res.status >= 500) {
      throw new AppError(
        "The server encountered an error. Please try again in a moment.",
        "SERVER_ERROR",
        res.status,
      );
    }
    let detail = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      detail = json?.detail ?? json?.message ?? detail;
    } catch {}
    throw new AppError(detail, undefined, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Auth / Identity ───────────────────────────────────────────────────────────

export function getMe(token: string, orgSlug?: string) {
  return apiFetch<User>("/auth/me", { token, orgSlug });
}

export function listOrgs(token: string) {
  return apiFetch<OrgEntry[]>("/auth/orgs", { token });
}

export function setupOrg(email: string, orgName: string, password: string) {
  return apiFetch<SetupOrgResponse>("/auth/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, org_name: orgName, password }),
  });
}

export function verifyInvite(token: string) {
  return apiFetch<InviteVerifyResponse>(`/auth/invite/verify?token=${token}`);
}

export function acceptInvite(
  inviteToken: string,
  password: string,
  fullName: string,
) {
  return apiFetch<AcceptInviteResponse>("/auth/invite/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: inviteToken, password, full_name: fullName }),
  });
}

export function updatePassword(token: string, newPassword: string) {
  return apiFetch<{ message: string }>("/auth/update-password", {
    token,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export function listOrgMembers(token: string, orgSlug?: string) {
  return apiFetch<OrgMember[]>("/auth/members", { token, orgSlug });
}

export function removeMember(token: string, userId: string, orgSlug?: string) {
  return apiFetch<{ message: string }>(`/auth/members/${userId}`, {
    token,
    orgSlug,
    method: "DELETE",
  });
}

export function inviteMember(
  token: string,
  email: string,
  orgSlug?: string,
  role: "ADMIN" | "MEMBER" = "MEMBER",
) {
  return apiFetch<{ message: string }>("/auth/invite", {
    token,
    orgSlug,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, role }),
  });
}

// Alias used by Sidebar
export function inviteByEmail(
  token: string,
  email: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string; invite_link?: string; already_registered?: boolean }>(
    "/auth/invite-by-email",
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    },
  );
}

export function getMembers(token: string, orgSlug?: string) {
  return apiFetch<OrgMember[]>("/auth/members", { token, orgSlug });
}

export function listMyOrgs(token: string) {
  return apiFetch<{ org_id: string; org_slug: string; org_name: string; role: string; is_active: boolean }[]>(
    "/auth/my-orgs",
    { token },
  );
}

// ── Workspaces ────────────────────────────────────────────────────────────────

export function listWorkspaces(token: string, orgSlug?: string) {
  return apiFetch<WorkspaceResponse[]>("/workspaces", {
    token,
    orgSlug,
  }).then((workspaces) => ({ workspaces }));
}

export function getWorkspace(
  token: string,
  workspaceId: string,
  orgSlug?: string,
) {
  return apiFetch<WorkspaceDetailResponse>(`/workspaces/${workspaceId}`, {
    token,
    orgSlug,
  });
}

export function createWorkspace(
  token: string,
  name: string,
  description: string,
  orgSlug?: string,
) {
  return apiFetch<WorkspaceResponse>("/workspaces", {
    token,
    orgSlug,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description }),
  });
}

// ── Documents ─────────────────────────────────────────────────────────────────

export function uploadDocument(
  token: string,
  workspaceId: string,
  file: File,
  orgSlug?: string,
) {
  const fd = new FormData();
  fd.append("file", file);
  return apiFetch<IngestJobQueued>(`/workspaces/${workspaceId}/documents`, {
    token,
    orgSlug,
    method: "POST",
    body: fd,
  });
}

export function getIngestStatus(
  token: string,
  jobId: string,
  orgSlug?: string,
) {
  return apiFetch<IngestJobStatus>(`/ingest/status/${jobId}`, {
    token,
    orgSlug,
  });
}

export function deleteDocument(
  token: string,
  workspaceId: string,
  documentId: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string }>(
    `/workspaces/${workspaceId}/documents/${documentId}`,
    { token, orgSlug, method: "DELETE" },
  );
}

// ── Sessions ──────────────────────────────────────────────────────────────────

// Uses the Redis-backed /session (singular) endpoint — this is what the goals
// router looks up via get_session(). The Postgres /sessions (plural) endpoint
// is for persistent session storage only and is NOT checked by goals.
export function createSession(
  token: string,
  workspaceId: string,
  documentIds: string[],
  orgSlug?: string,
) {
  return apiFetch<{ session_id: string }>(
    `/workspaces/${workspaceId}/session`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: documentIds }),
    },
  );
}

// ── Goals ─────────────────────────────────────────────────────────────────────

export function createGoal(
  token: string,
  workspaceId: string,
  goalText: string,
  sessionId?: string,
  orgSlug?: string,
) {
  const body: Record<string, unknown> = { goal_text: goalText };
  if (sessionId) body.session_id = sessionId;
  return apiFetch<CreateGoalResponse>(`/workspaces/${workspaceId}/goals`, {
    token,
    orgSlug,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listGoals(
  token: string,
  workspaceId: string,
  orgSlug?: string,
) {
  return apiFetch<{ goals: GoalSummary[]; total: number }>(
    `/workspaces/${workspaceId}/goals`,
    { token, orgSlug },
  );
}

export function getGoalDetail(
  token: string,
  workspaceId: string,
  goalId: string,
  orgSlug?: string,
) {
  return apiFetch<GoalDetail>(`/workspaces/${workspaceId}/goals/${goalId}`, {
    token,
    orgSlug,
  });
}

export function confirmGoalIntent(
  token: string,
  workspaceId: string,
  goalId: string,
  intent: GoalIntent,
  orgSlug?: string,
) {
  return apiFetch<ConfirmIntentResponse>(
    `/workspaces/${workspaceId}/goals/${goalId}/confirm-intent`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmed_intent: intent }),
    },
  );
}

export function deleteGoal(
  token: string,
  workspaceId: string,
  goalId: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string }>(
    `/workspaces/${workspaceId}/goals/${goalId}`,
    { token, orgSlug, method: "DELETE" },
  );
}

export function getGoalResult(
  token: string,
  workspaceId: string,
  goalId: string,
  orgSlug?: string,
) {
  return apiFetch<{
    goal_id: string;
    status: string;
    intent?: string | null;
    answer?: string | null;
    findings?: unknown[];
  }>(`/workspaces/${workspaceId}/goals/${goalId}/result`, { token, orgSlug });
}

// ── Workflow (ACT path) ───────────────────────────────────────────────────────

export function getWorkflowStatus(
  token: string,
  workflowId: string,
  orgSlug?: string,
) {
  return apiFetch<WorkflowStatusResponse>(`/agent/status/${workflowId}`, {
    token,
    orgSlug,
  });
}

export function getWorkflowBrief(
  token: string,
  workflowId: string,
  orgSlug?: string,
) {
  return apiFetch<WorkflowBriefResponse>(`/agent/${workflowId}/brief`, {
    token,
    orgSlug,
  });
}

export function confirmBrief(
  token: string,
  workflowId: string,
  proceed: boolean,
  orgSlug?: string,
) {
  return apiFetch<{ workflow_id: string; status: string; message: string }>(
    `/agent/confirm-brief/${workflowId}`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proceed }),
    },
  );
}

export function getWorkflowActions(
  token: string,
  workflowId: string,
  orgSlug?: string,
) {
  return apiFetch<GetActionsResponse>(`/agent/${workflowId}/actions`, {
    token,
    orgSlug,
  });
}

export function approveWorkflow(
  token: string,
  workflowId: string,
  orgSlug?: string,
  updatedDraft?: string,
  resolvedMissing?: Record<string, string>,
) {
  return apiFetch<{
    workflow_id: string;
    status: string;
    draft_r2_key?: string;
  }>(`/agent/approve/${workflowId}`, {
    token,
    orgSlug,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      updated_draft: updatedDraft ?? null,
      resolved_missing: resolvedMissing ?? null,
    }),
  });
}

export function rejectWorkflow(
  token: string,
  workflowId: string,
  reason: string,
  orgSlug?: string,
) {
  return apiFetch<{ workflow_id: string; status: string }>(
    `/agent/reject/${workflowId}`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
}

// ── Deadlines ─────────────────────────────────────────────────────────────────

export function listDeadlines(
  token: string,
  workspaceId: string,
  orgSlug?: string,
) {
  return apiFetch<DeadlineItem[]>(
    `/workspaces/${workspaceId}/deadlines`,
    { token, orgSlug },
  );
}

// ── Escalations ───────────────────────────────────────────────────────────────

export function listEscalations(token: string, orgSlug?: string) {
  return apiFetch<EscalationItem[]>("/escalations", { token, orgSlug });
}

export function resolveEscalation(
  token: string,
  escalationId: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string }>(
    `/escalations/${escalationId}/resolve`,
    { token, orgSlug, method: "POST" },
  );
}

// ── Notifications ─────────────────────────────────────────────────────────────

export function listNotifications(token: string, orgSlug?: string) {
  return apiFetch<NotificationItem[]>("/notifications", { token, orgSlug });
}

export function markNotificationRead(
  token: string,
  notificationId: string,
  orgSlug?: string,
) {
  return apiFetch<{ status: string }>(
    `/notifications/${notificationId}/read`,
    { token, orgSlug, method: "POST" },
  );
}

export function markAllNotificationsRead(token: string, orgSlug?: string) {
  return apiFetch<{ status: string }>("/notifications/read-all", {
    token,
    orgSlug,
    method: "POST",
  });
}

// ── Audit ─────────────────────────────────────────────────────────────────────

export function getGoalAuditTrail(
  token: string,
  workspaceId: string,
  goalId: string,
  orgSlug?: string,
) {
  return apiFetch<{
    goal_id: string;
    goal_text: string;
    events: { timestamp: string; type: string; detail: Record<string, unknown> }[];
  }>(`/workspaces/${workspaceId}/goals/${goalId}/audit`, { token, orgSlug });
}

// ── Legacy compat stubs ───────────────────────────────────────────────────────
// These are used by old sidebar/hook components that haven't been rewritten yet.

export function getDocumentStatus(token: string, jobId: string, orgSlug?: string) {
  return getIngestStatus(token, jobId, orgSlug);
}

export function getGoalStatus(token: string, workspaceId: string, goalId: string, orgSlug?: string) {
  return getGoalDetail(token, workspaceId, goalId, orgSlug);
}

export function getMyOrgs(token: string) {
  return listOrgs(token);
}

export function checkOrgAvailable(slug: string) {
  return apiFetch<{ available: boolean }>(`/auth/check-org?slug=${slug}`);
}

export function setupOrgAdmin(
  _accessToken: string,
  params: { org_id?: string; org_name: string },
) {
  // Legacy call from setup/page — maps to public registration endpoint
  return apiFetch<{ message: string }>("/auth/setup-org", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}
