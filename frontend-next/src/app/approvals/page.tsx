"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  listApprovals,
  approveWorkflowToken,
  rejectWorkflowToken,
} from "@/lib/api";
import type { ApprovalRequest } from "@/lib/types";

const URGENCY_COLORS: Record<string, string> = {
  "0.1": "slate",
  "0.3": "blue",
  "0.5": "yellow",
  "0.7": "orange",
  "0.9": "red",
};

function getUrgencyColor(score: number): string {
  if (score >= 0.9) return "error";
  if (score >= 0.7) return "warning";
  if (score >= 0.5) return "warning";
  return "default";
}

export default function ApprovalsPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [orgSlug, setOrgSlug] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    setToken(localStorage.getItem("sb-access-token"));
    setOrgSlug(localStorage.getItem("sb-org-slug"));
  }, []);

  const fetchApprovals = useCallback(async () => {
    if (!token) return;
    try {
      const data = await listApprovals(token, orgSlug || undefined);
      setApprovals(data);
    } catch (err) {
      console.error("Failed to load approvals:", err);
    } finally {
      setLoading(false);
    }
  }, [token, orgSlug]);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals]);

  const handleApprove = async (approval: ApprovalRequest) => {
    if (!token || !approval.token_hash) return;
    setActionLoading(approval.id);
    try {
      await approveWorkflowToken(
        token,
        approval.workflow_id,
        approval.token_hash,
        orgSlug || undefined,
      );
      // Remove from list after approval
      setApprovals((prev) => prev.filter((a) => a.id !== approval.id));
    } catch (err) {
      console.error("Approval failed:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (approval: ApprovalRequest) => {
    if (!token || !approval.token_hash) return;
    setActionLoading(approval.id);
    try {
      await rejectWorkflowToken(
        token,
        approval.workflow_id,
        approval.token_hash,
        orgSlug || undefined,
      );
      // Remove from list after rejection
      setApprovals((prev) => prev.filter((a) => a.id !== approval.id));
    } catch (err) {
      console.error("Rejection failed:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const pendingApprovals = approvals.filter((a) => a.status === "PENDING");

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold text-white mb-2">Approval Inbox</h1>
      <p className="text-slate-400 text-sm mb-8">
        Review and approve or reject drafted actions before execution.
      </p>

      {loading ? (
        <Card className="p-8 text-center">
          <p className="text-slate-400">Loading approvals...</p>
        </Card>
      ) : pendingApprovals.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-slate-400">No pending approvals.</p>
          <p className="text-slate-500 text-sm mt-2">
            Drafted actions requiring your sign-off will appear here.
          </p>
        </Card>
      ) : (
        <div className="space-y-4">
          {pendingApprovals.map((approval) => (
            <Card key={approval.id} className="p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant={getUrgencyColor(approval.urgency_score)}>
                      Urgency: {(approval.urgency_score * 100).toFixed(0)}%
                    </Badge>
                    <Badge variant="info">{approval.action_type}</Badge>
                    <Badge variant="warning">{approval.status}</Badge>
                  </div>
                  <p className="text-white text-sm font-medium mt-2">
                    {approval.description}
                  </p>
                  <p className="text-slate-500 text-xs mt-1">
                    Workflow: {approval.workflow_id.slice(0, 8)}...
                    &nbsp;·&nbsp;Expires:{" "}
                    {new Date(approval.expires_at).toLocaleString()}
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleReject(approval)}
                    disabled={actionLoading === approval.id}
                  >
                    {actionLoading === approval.id ? "..." : "Reject"}
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => handleApprove(approval)}
                    disabled={actionLoading === approval.id}
                  >
                    {actionLoading === approval.id ? "..." : "Approve"}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
