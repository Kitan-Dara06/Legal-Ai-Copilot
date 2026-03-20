// ── API Service Layer ────────────────────────────────────────────────────────
// Typed wrapper around fetch. Reads Supabase token from cookie via
// a getter passed in at call time (works in both server + client components).

import type {
    AskResponse,
    AcceptInviteResponse,
    FileListResponse,
    FileStatusResponse,
    InviteVerifyResponse,
    OrgEntry,
    SessionResponse,
    SetupOrgResponse,
    UploadResult,
    User,
    OrgMember,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Headers = Record<string, string>;

/** Custom error that carries the backend's structured `code` field (e.g. "setup_required"). */
export class AppError extends Error {
    code?: string;
    status?: number;
    constructor(message: string, code?: string, status?: number) {
        super(message);
        this.name = "AppError";
        this.code = code;
        this.status = status;
    }
}

function buildHeaders(token?: string | null, orgSlug?: string | null): Headers {
    const h: Headers = {};
    if (token) h["Authorization"] = `Bearer ${token}`;
    if (orgSlug) h["X-Active-Org"] = orgSlug;
    return h;
}

async function apiFetch<T>(
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
            // detail can be a string or an object like {code, message}
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


// ── Auth ─────────────────────────────────────────────────────────────────────

export function getMe(token: string) {
    return apiFetch<User>("/auth/me", { token });
}

export async function getMyOrgs(token: string) {
    const res = await apiFetch<{ orgs: OrgEntry[] }>("/auth/my-orgs", { token });
    return res.orgs;
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

// ── Magic Invite ─────────────────────────────────────────────────────────────

export function verifyInviteToken(inviteToken: string) {
    return apiFetch<InviteVerifyResponse>(
        `/invites/verify?token=${encodeURIComponent(inviteToken)}`,
    );
}

export function acceptInvite(payload: {
    token: string;
    full_name: string;
    password: string;
}) {
    return apiFetch<AcceptInviteResponse>("/invites/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
}

export function acceptExistingInvite(token: string, inviteToken: string) {
    return apiFetch<{ message: string; org_id: string }>(
        "/auth/accept-invite-by-token",
        {
            token,
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: inviteToken }),
        },
    );
}

// ── Files ────────────────────────────────────────────────────────────────────

export function listFiles(token: string, orgSlug?: string) {
    return apiFetch<FileListResponse>("/files/list", { token, orgSlug });
}

export async function uploadFiles(
    token: string,
    files: File[],
    orgSlug?: string,
    onProgress?: (pct: number) => void,
): Promise<{ results: UploadResult[] }> {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_URL}/files/upload`);
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
        files.forEach((f) => fd.append("files", f));
        xhr.send(fd);
    });
}

export function getFileStatus(token: string, fileId: number, orgSlug?: string) {
    return apiFetch<FileStatusResponse>(`/files/${fileId}/status`, {
        token,
        orgSlug,
    });
}

export function deleteFile(token: string, fileId: number, orgSlug?: string) {
    return apiFetch<{ message: string }>(`/files/${fileId}`, {
        token,
        orgSlug,
        method: "DELETE",
    });
}

// ── Sessions ─────────────────────────────────────────────────────────────────

export function createSession(
    token: string,
    fileIds: number[],
    orgSlug?: string,
) {
    return apiFetch<SessionResponse>("/session/", {
        token,
        orgSlug,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fileIds),
    });
}

export function getSession(token: string, sessionId: string, orgSlug?: string) {
    return apiFetch<SessionResponse>(`/session/${sessionId}`, {
        token,
        orgSlug,
    });
}

export async function uploadToSession(
    token: string,
    sessionId: string,
    file: File,
    orgSlug?: string,
): Promise<unknown> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API_URL}/session/${sessionId}/upload`, {
        method: "POST",
        headers: buildHeaders(token, orgSlug),
        body: fd,
    });
    if (!res.ok) throw new Error(`Upload to session failed: ${res.statusText}`);
    return res.json();
}

export function deleteSession(
    token: string,
    sessionId: string,
    orgSlug?: string,
) {
    return apiFetch<{ message: string }>(`/session/${sessionId}`, {
        token,
        orgSlug,
        method: "DELETE",
    });
}

// ── Chat ─────────────────────────────────────────────────────────────────────

export function askQuestion(
    token: string,
    sessionId: string,
    question: string,
    mode: "fast" | "hybrid",
    orgSlug?: string,
) {
    const params = new URLSearchParams({
        session_id: sessionId,
        question,
        mode,
    });
    return apiFetch<AskResponse>(`/ask?${params}`, {
        token,
        orgSlug,
        method: "POST",
    });
}

export function askAgent(
    token: string,
    sessionId: string,
    question: string,
    orgSlug?: string,
) {
    const params = new URLSearchParams({
        session_id: sessionId,
        question,
        mode: "hybrid",
    });
    return apiFetch<AskResponse>(`/ask-agent?${params}`, {
        token,
        orgSlug,
        method: "POST",
    });
}
