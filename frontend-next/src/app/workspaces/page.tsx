"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { listWorkspaces, createWorkspace } from "@/lib/api";
import type { WorkspaceResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import {
  Plus, FolderOpen, FileText, Clock,
  ChevronRight, X, Loader2,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

function statusBadge(s: string) {
  if (s === "READY")   return <Badge variant="success">Ready</Badge>;
  if (s === "PARTIAL") return <Badge variant="warning">Partial</Badge>;
  if (s === "PENDING") return <Badge variant="muted">Pending</Badge>;
  return <Badge variant="muted">{s}</Badge>;
}

export default function WorkspacesPage() {
  const router = useRouter();
  const supabase = createClient();

  const [token, setToken]           = useState<string | null>(null);
  const [orgSlug, setOrgSlug]       = useState<string | undefined>();
  const [workspaces, setWorkspaces] = useState<WorkspaceResponse[]>([]);
  const [loading, setLoading]       = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating]     = useState(false);
  const [newName, setNewName]       = useState("");
  const [newDesc, setNewDesc]       = useState("");

  useEffect(() => {
    supabase.auth.getSession().then(({ data }: any) => {
      if (!data?.session) { router.push("/login"); return; }
      setToken(data?.session?.access_token);
      setOrgSlug(data?.session?.user?.user_metadata?.org_slug);
    });
  }, []);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    listWorkspaces(token, orgSlug)
      .then((r) => setWorkspaces(r.workspaces ?? []))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, orgSlug]);

  const handleCreate = async () => {
    if (!token || !newName.trim()) return;
    setCreating(true);
    try {
      const ws = await createWorkspace(token, newName.trim(), newDesc.trim(), orgSlug);
      setWorkspaces((prev) => [ws, ...prev]);
      setShowCreate(false);
      setNewName(""); setNewDesc("");
    } catch (e) { console.error(e); }
    finally { setCreating(false); }
  };

  return (
    <div className="max-w-[1000px] mx-auto px-6 py-10">

      {/* Header */}
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-[#F0EEE9] tracking-tight">Workspaces</h1>
          <p className="text-sm text-[#7A7A8A] mt-1">Your matters, deals, and document sets</p>
        </div>
        <Button variant="gold" onClick={() => setShowCreate(true)}>
          <Plus className="w-4 h-4" />
          New Workspace
        </Button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="mb-6 p-5 rounded-xl border border-[#D4A853]/25 bg-[#D4A853]/[0.04] animate-slide-up">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-[#F0EEE9]">New Workspace</h3>
            <button onClick={() => setShowCreate(false)} className="text-[#7A7A8A] hover:text-[#F0EEE9] transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="space-y-3">
            <input
              autoFocus
              type="text"
              placeholder="Workspace name  e.g. TechCorp Acquisition"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
              className="w-full bg-[#1E1E28] border border-[#2A2A32] focus:border-[#D4A853] rounded-lg px-4 py-2.5 text-sm text-[#F0EEE9] placeholder-[#4A4A5A] outline-none transition-colors"
            />
            <input
              type="text"
              placeholder="Description  (optional)"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              className="w-full bg-[#1E1E28] border border-[#2A2A32] focus:border-[#D4A853] rounded-lg px-4 py-2.5 text-sm text-[#F0EEE9] placeholder-[#4A4A5A] outline-none transition-colors"
            />
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button>
              <Button variant="gold" size="sm" loading={creating} disabled={!newName.trim()} onClick={handleCreate}>
                Create
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-8 h-8 text-[#D4A853] animate-spin" />
        </div>
      ) : workspaces.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
          <div className="w-16 h-16 rounded-2xl bg-[#16161D] border border-[#2A2A32] flex items-center justify-center">
            <FolderOpen className="w-7 h-7 text-[#4A4A5A]" strokeWidth={1.5} />
          </div>
          <div>
            <p className="text-[#F0EEE9] font-medium">No workspaces yet</p>
            <p className="text-sm text-[#7A7A8A] mt-1">Create a workspace to start uploading documents</p>
          </div>
          <Button variant="gold" onClick={() => setShowCreate(true)}>
            <Plus className="w-4 h-4" />
            Create First Workspace
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {workspaces.map((ws) => (
            <button
              key={ws.workspace_id}
              onClick={() => router.push(`/workspaces/${ws.workspace_id}`)}
              className="w-full flex items-center justify-between p-5 rounded-xl border border-[#2A2A32] bg-[#16161D] hover:border-[#363644] hover:bg-[#1E1E28] transition-all duration-150 group text-left"
            >
              <div className="flex items-center gap-4 min-w-0">
                <div className="w-10 h-10 rounded-lg bg-[#1E1E28] border border-[#2A2A32] group-hover:border-[#D4A853]/30 flex items-center justify-center shrink-0 transition-colors">
                  <FolderOpen className="w-5 h-5 text-[#7A7A8A] group-hover:text-[#D4A853] transition-colors" strokeWidth={1.5} />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-[#F0EEE9] truncate">{ws.name}</p>
                  {ws.description && (
                    <p className="text-xs text-[#7A7A8A] truncate mt-0.5">{ws.description}</p>
                  )}
                  <div className="flex items-center gap-3 mt-1.5">
                    <span className="flex items-center gap-1 text-[10px] text-[#4A4A5A]">
                      <FileText className="w-3 h-3" />
                      {ws.document_count} doc{ws.document_count !== 1 ? "s" : ""}
                    </span>
                    {ws.last_active_at && (
                      <span className="flex items-center gap-1 text-[10px] text-[#4A4A5A]">
                        <Clock className="w-3 h-3" />
                        {formatDistanceToNow(new Date(ws.last_active_at), { addSuffix: true })}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0 ml-4">
                {statusBadge(ws.intelligence_status)}
                <ChevronRight className="w-4 h-4 text-[#4A4A5A] group-hover:text-[#7A7A8A] transition-colors" />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
