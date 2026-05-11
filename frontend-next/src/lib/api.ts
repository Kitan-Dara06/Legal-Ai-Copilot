// ── API Service Layer ────────────────────────────────────────────────────────
// Typed wrapper around fetch. Reads Supabase token from cookie via
// a getter passed in at call time (works in both server + client components).

import type {
  AcceptInviteResponse,
  InviteVerifyResponse,
  OrgEntry,
  SetupOrgResponse,
  User,
  OrgMember,
  WorkspaceResponse,
  WorkspaceDetailResponse,
  CreateGoalResponse,
  GoalDetail,
  GoalSummary,
  GoalIntent,
  ConfirmIntentResponse,
  NotificationItem,
  ApprovalRequest,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

if (!API_URL) {
  if (typeof window !== "undefined") {
    console.error("NEXT_PUBLIC_API_URL is not defined. API calls will fail.");
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

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
  const h: Headers = { "Content-Type": "application/json" };
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
  console.log(`[apiFetch] Calling ${url}...`);

  const res = await fetch(url, {
    ...rest,
    headers: {
      ...buildHeaders(token, orgSlug),
      ...(extraHeaders as Record<string, string>),
    },
  }).catch((err) => {
    console.error(`[apiFetch] Network error calling ${url}:`, err);
    throw err;
  });

  if (!res.ok) {
    console.error(`[apiFetch] HTTP ${res.status} for ${url}`);
    let message = res.statusText;
    let code: string | undefined;
    try {
      const body = await res.json();
      if (body.detail && typeof body.detail === "object") {
        code = body.detail.code;
        message = body.detail.message || JSON.stringify(body.detail);
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      /* ignore */
    }

    if (res.status === 401) {
      console.warn(`[apiFetch] 401 Unauthorized for ${url}`);
      if (typeof window !== "undefined") window.location.href = "/login";
    }

    throw new AppError(message, code, res.status);
  }

  return res.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function checkOrgAvailable(orgId: string) {
  const res = await fetch(
    `${API_URL}/auth/check-org?org_id=${encodeURIComponent(orgId)}`,
  );
  const data = await res.json();
  return data as { available: boolean };
}

export function getMe(token: string, orgSlug?: string) {
  return apiFetch<User>("/auth/me", { token, orgSlug });
}

export async function getMyOrgs(token: string) {
  const res = await apiFetch<OrgEntry[]>("/auth/my-orgs", { token });
  return res;
}

export function setupOrg(
  token: string,
  payload: { org_id: string; org_name?: string },
) {
  return apiFetch<SetupOrgResponse>("/auth/setup-org", {
    token,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function inviteByEmail(token: string, email: string, orgSlug?: string) {
  return apiFetch<{ message: string }>("/auth/invite-by-email", {
    token,
    orgSlug,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export function getMembers(token: string, orgSlug?: string) {
  return apiFetch<OrgMember[]>("/auth/members", { token, orgSlug });
}

export function removeMember(token: string, userId: string, orgSlug?: string) {
  return apiFetch<{ message: string }>(`/auth/members/${userId}`, {
    token,
    orgSlug,
    method: "DELETE",
  });
}

// ── Invites ───────────────────────────────────────────────────────────────────

export function verifyInviteToken(token: string) {
  return apiFetch<InviteVerifyResponse>(
    `/invites/verify?token=${encodeURIComponent(token)}`,
  );
}

export function acceptInvite(payload: {
  token: string;
  full_name?: string;
  password?: string;
}) {
  return apiFetch<AcceptInviteResponse>("/invites/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function acceptExistingInvite(token: string, inviteToken: string) {
  return apiFetch<{ message: string }>("/auth/accept-invite-by-token", {
    token,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: inviteToken }),
  });
}

// ── Workspaces ────────────────────────────────────────────────────────────────

export function listWorkspaces(token: string, orgSlug?: string) {
  return apiFetch<WorkspaceResponse[]>("/workspaces", { token, orgSlug });
}

export function getWorkspace(
  token: string,
  workspaceId: string,
  orgSlug?: string,
) {
  return apiFetch<WorkspaceDetailResponse>(
    `/workspaces/${workspaceId}?include_docs=true`,
    {
      token,
      orgSlug,
    },
  );
}

export function createWorkspace(
  token: string,
  name: string,
  description?: string,
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

// ── Workspace Documents ───────────────────────────────────────────────────────

export async function uploadDocument(
  token: string,
  workspaceId: string,
  file: File,
  orgSlug?: string,
  onProgress?: (pct: number) => void,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/workspaces/${workspaceId}/documents`);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    if (orgSlug) xhr.setRequestHeader("X-Active-Org", orgSlug);

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable)
          onProgress(Math.round((e.loaded / e.total) * 100));
      });
    }

    xhr.onload = () => {
      if (xhr.status === 202) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`Upload failed: HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));

    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
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

export function getDocumentStatus(
  token: string,
  workspaceId: string,
  documentId: string,
  orgSlug?: string,
) {
  return apiFetch<{
    document_id: string;
    status: string;
    stages?: Record<string, boolean> | null;
    error?: string | null;
  }>(`/workspaces/${workspaceId}/documents/${documentId}/status`, {
    token,
    orgSlug,
  });
}

// ── Workspace Sessions (scoped document subset) ───────────────────────────────

export function createWorkspaceSession(
  token: string,
  workspaceId: string,
  documentIds: string[],
  orgSlug?: string,
) {
  return apiFetch<{ session_id: string }>(
    `/workspaces/${workspaceId}/sessions`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: documentIds }),
    },
  );
}

export function getWorkspaceSession(
  token: string,
  workspaceId: string,
  sessionId: string,
  orgSlug?: string,
) {
  return apiFetch<{
    session_id: string;
    workspace_id: string;
    documents: { document_id: string; filename: string; status: string }[];
  }>(`/workspaces/${workspaceId}/sessions/${sessionId}`, { token, orgSlug });
}

export function createWorkspaceScopedSession(
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

// ── Unified Goals API ────────────────────────────────────────────────────────

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

export function getGoalStatus(
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
    actions?: {
      id: string;
      action_type: string;
      description: string;
      status: string;
      urgency: number;
    }[];
    logs?: {
      id: string;
      tool_name: string;
      status: string;
      summary: string;
      created_at: string;
    }[];
  }>(`/workspaces/${workspaceId}/goals/${goalId}/result`, { token, orgSlug });
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

export function confirmGoalPlan(
  token: string,
  workspaceId: string,
  goalId: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string }>(
    `/workspaces/${workspaceId}/goals/${goalId}/confirm-plan`,
    { token, orgSlug, method: "POST" },
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

// ── Approvals ────────────────────────────────────────────────────────────────

export function listApprovals(token: string, orgSlug?: string) {
  return apiFetch<ApprovalRequest[]>("/approvals", { token, orgSlug });
}

export function getApprovalDetail(
  token: string,
  approvalId: string,
  orgSlug?: string,
) {
  return apiFetch<ApprovalRequest>(`/approvals/${approvalId}`, {
    token,
    orgSlug,
  });
}

export function approveWorkflowToken(
  token: string,
  workflowId: string,
  approvalToken: string,
  orgSlug?: string,
) {
  return apiFetch<{ workflow_id: string; status: string }>(
    `/approvals/workflows/${workflowId}/approve`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: approvalToken }),
    },
  );
}

export function rejectWorkflowToken(
  token: string,
  workflowId: string,
  approvalToken: string,
  orgSlug?: string,
) {
  return apiFetch<{ workflow_id: string; status: string }>(
    `/approvals/workflows/${workflowId}/reject`,
    {
      token,
      orgSlug,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: approvalToken }),
    },
  );
}

export function reissueApproval(
  token: string,
  approvalId: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string }>(`/approvals/${approvalId}/reissue`, {
    token,
    orgSlug,
    method: "POST",
  });
}

// ── Notifications ────────────────────────────────────────────────────────────

export function listNotifications(token: string, orgSlug?: string) {
  return apiFetch<NotificationItem[]>("/notifications", { token, orgSlug });
}

export function markNotificationRead(
  token: string,
  notificationId: string,
  orgSlug?: string,
) {
  return apiFetch<{ status: string }>(`/notifications/${notificationId}/read`, {
    token,
    orgSlug,
    method: "POST",
  });
}

export function markAllNotificationsRead(token: string, orgSlug?: string) {
  return apiFetch<{ status: string }>("/notifications/read-all", {
    token,
    orgSlug,
    method: "POST",
  });
}

// ── Audit ────────────────────────────────────────────────────────────────────

export function getGoalAuditTrail(
  token: string,
  workspaceId: string,
  goalId: string,
  orgSlug?: string,
) {
  return apiFetch<{
    goal_id: string;
    goal_text: string;
    events: {
      timestamp: string;
      type: string;
      detail: Record<string, unknown>;
    }[];
  }>(`/workspaces/${workspaceId}/goals/${goalId}/audit`, { token, orgSlug });
}

// ── Deadlines ────────────────────────────────────────────────────────────────

export function listDeadlines(
  token: string,
  workspaceId: string,
  orgSlug?: string,
) {
  return apiFetch<
    {
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
    }[]
  >(`/workspaces/${workspaceId}/deadlines`, { token, orgSlug });
}

// ── Escalations ──────────────────────────────────────────────────────────────

export function listEscalations(token: string, orgSlug?: string) {
  return apiFetch<
    {
      id: string;
      status: string;
      intent: string;
      error_context?: string;
      created_at: string;
    }[]
  >("/escalations", { token, orgSlug });
}

export function resolveEscalation(
  token: string,
  escalationId: string,
  orgSlug?: string,
) {
  return apiFetch<{ message: string }>(`/escalations/${escalationId}/resolve`, {
    token,
    orgSlug,
    method: "POST",
  });
}
