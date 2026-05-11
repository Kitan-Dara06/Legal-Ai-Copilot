"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { listWorkspaces, createWorkspace } from "@/lib/api";
import { WorkspaceResponse } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";

export default function WorkspacesPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [orgSlug, setOrgSlug] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  useEffect(() => {
    const stored = localStorage.getItem("sb-access-token");
    const org = localStorage.getItem("sb-org-slug");
    if (stored) setToken(stored);
    if (org) setOrgSlug(org);
  }, []);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    listWorkspaces(token, orgSlug || undefined)
      .then(setWorkspaces)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, orgSlug]);

  const handleCreate = async () => {
    if (!token || !newName.trim()) return;
    try {
      const ws = await createWorkspace(
        token,
        newName.trim(),
        newDesc.trim() || undefined,
        orgSlug || undefined,
      );
      setWorkspaces((prev) => [ws, ...prev]);
      setShowCreate(false);
      setNewName("");
      setNewDesc("");
    } catch (e) {
      console.error("Failed to create workspace:", e);
    }
  };

  const statusColor = (
    status: string,
  ): "success" | "warning" | "error" | "info" | "default" => {
    switch (status) {
      case "READY":
        return "success";
      case "PENDING":
        return "warning";
      case "PARTIAL":
        return "warning";
      default:
        return "default";
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Workspaces</h1>
          <p className="text-slate-400 text-sm mt-1">
            Manage your deal folders and documents
          </p>
        </div>
        <Button onClick={() => setShowCreate(!showCreate)}>
          {showCreate ? "Cancel" : "New Workspace"}
        </Button>
      </div>

      {showCreate && (
        <Card className="mb-6 p-4 space-y-3">
          <Input
            placeholder="Workspace name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <Input
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <Button onClick={handleCreate} disabled={!newName.trim()}>
            Create
          </Button>
        </Card>
      )}

      {loading ? (
        <div className="text-slate-400 text-center py-12">
          Loading workspaces...
        </div>
      ) : workspaces.length === 0 ? (
        <div className="text-slate-400 text-center py-12">
          <p className="text-lg">No workspaces yet</p>
          <p className="text-sm mt-2">
            Create your first workspace to get started
          </p>
        </div>
      ) : (
        <div className="grid gap-4">
          {workspaces.map((ws) => (
            <Card
              key={ws.workspace_id}
              className="p-4 cursor-pointer hover:border-blue-500 transition-colors"
              onClick={() => router.push(`/workspaces/${ws.workspace_id}`)}
            >
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-white font-semibold">{ws.name}</h3>
                  {ws.description && (
                    <p className="text-slate-400 text-sm mt-1">
                      {ws.description}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-slate-400 text-sm">
                    {ws.document_count} documents
                  </span>
                  <Badge variant={statusColor(ws.intelligence_status)}>
                    {ws.intelligence_status}
                  </Badge>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
