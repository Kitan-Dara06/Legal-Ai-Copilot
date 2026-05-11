"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import {
  getApprovalDetail,
  approveWorkflowToken,
  rejectWorkflowToken,
} from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import type { ApprovalRequest } from "@/lib/types";
import { Scale, ArrowLeft } from "lucide-react";
import Link from "next/link";

export default function ApprovalDetailPage() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const tokenParam = searchParams.get("token");

  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState<string>("");
  const [orgSlug, setOrgSlug] = useState<string | undefined>();
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth
      .getSession()
      .then(
        ({
          data: { session },
        }: {
          data: { session: import("@supabase/supabase-js").Session | null };
        }) => {
          if (session) {
            setToken(session.access_token);
            setOrgSlug(
              localStorage.getItem("legalrag_active_org") || undefined,
            );
          }
        },
      );
  }, []);

  useEffect(() => {
    if (!token || !id) return;
    getApprovalDetail(token, id, orgSlug)
      .then(setApproval)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, id, orgSlug]);

  const handleApprove = async () => {
    if (!approval || !token) return;
    const approveToken = tokenParam || approval.token_hash || "";
    setActionLoading("approve");
    try {
      await approveWorkflowToken(
        token,
        approval.workflow_id,
        approveToken,
        orgSlug,
      );
      setMessage("Action approved successfully!");
      setApproval((prev) => (prev ? { ...prev, status: "APPROVED" } : prev));
    } catch (err: any) {
      setMessage(`Approval failed: ${err.message}`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async () => {
    if (!approval || !token) return;
    const approveToken = tokenParam || approval.token_hash || "";
    setActionLoading("reject");
    try {
      await rejectWorkflowToken(
        token,
        approval.workflow_id,
        approveToken,
        orgSlug,
      );
      setMessage("Action rejected.");
      setApproval((prev) => (prev ? { ...prev, status: "REJECTED" } : prev));
    } catch (err: any) {
      setMessage(`Rejection failed: ${err.message}`);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-navy-950 flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-accent-blue border-t-transparent animate-spin" />
      </div>
    );
  }

  if (!approval) {
    return (
      <div className="min-h-screen bg-navy-950 flex items-center justify-center">
        <p className="text-slate-400">Approval request not found.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-navy-950">
      <div className="max-w-4xl mx-auto px-4 py-8">
        <Link
          href="/approvals"
          className="inline-flex items-center gap-2 text-slate-400 hover:text-white mb-6 text-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Approvals
        </Link>

        <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-xl font-bold text-white">Approval Request</h1>
            <span
              className={`text-xs font-semibold px-2 py-1 rounded ${
                approval.status === "PENDING"
                  ? "bg-amber-500/20 text-amber-400"
                  : approval.status === "APPROVED"
                    ? "bg-emerald-500/20 text-emerald-400"
                    : approval.status === "REJECTED"
                      ? "bg-red-500/20 text-red-400"
                      : "bg-slate-500/20 text-slate-400"
              }`}
            >
              {approval.status}
            </span>
          </div>

          <p className="text-slate-300 text-sm mb-2">{approval.description}</p>
          <p className="text-slate-500 text-xs mb-4">
            Type: {approval.action_type} | Urgency:{" "}
            {(approval.urgency_score * 100).toFixed(0)}%
          </p>

          {approval.expires_at && (
            <p className="text-xs text-slate-500">
              Expires: {new Date(approval.expires_at).toLocaleString()}
            </p>
          )}
        </div>

        {message && (
          <div
            className={`p-4 rounded-lg mb-4 text-sm ${
              message.includes("failed") || message.includes("Failed")
                ? "bg-red-900/30 text-red-400"
                : "bg-emerald-900/30 text-emerald-400"
            }`}
          >
            {message}
          </div>
        )}

        {approval.status === "PENDING" && (
          <div className="flex gap-4">
            <button
              onClick={handleApprove}
              disabled={actionLoading !== null}
              className="flex-1 px-6 py-3 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-700 text-white font-semibold rounded-lg transition-colors"
            >
              {actionLoading === "approve" ? "Processing..." : "✅ Approve"}
            </button>
            <button
              onClick={handleReject}
              disabled={actionLoading !== null}
              className="flex-1 px-6 py-3 bg-red-600 hover:bg-red-700 disabled:bg-slate-700 text-white font-semibold rounded-lg transition-colors"
            >
              {actionLoading === "reject" ? "Processing..." : "❌ Reject"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
