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
