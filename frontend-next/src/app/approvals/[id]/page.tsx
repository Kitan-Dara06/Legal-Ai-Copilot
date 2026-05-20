"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import {
  getApprovalDetail,
  approveWorkflowToken,
  rejectWorkflowToken,
  getWorkflowActions,
} from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import type { ApprovalRequest } from "@/lib/types";
import { Scale, ArrowLeft, Download, CheckCircle, FileText } from "lucide-react";
import Link from "next/link";

interface DraftPayload {
  draft_text?: string;
  source_citations?: string[];
  grounding_score?: number;
  missing_info?: string[];
  grounding_failed?: boolean;
  qa?: { has_issues: boolean; summary: string };
}

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
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [draftPayload, setDraftPayload] = useState<DraftPayload | null>(null);
  const [approved, setApproved] = useState(false);

  useEffect(() => {
    void (async () => {
      const supabase = createClient();
      const { data } = await supabase.auth.getSession();
      if (data.session) {
        setToken(data.session.access_token);
        setOrgSlug(localStorage.getItem("legalrag_active_org") || undefined);
      }
    })();
  }, []);

  useEffect(() => {
    if (!token || !id) return;
    getApprovalDetail(token, id, orgSlug)
      .then(setApproval)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, id, orgSlug]);

  // Load draft content when we have the workflow ID
  useEffect(() => {
    if (!token || !approval?.workflow_id) return;
    getWorkflowActions(token, approval.workflow_id, orgSlug)
      .then((data) => {
        // Find the first action with a draft
        const withDraft = data.actions.find(
          (a: any) => a.draft_payload?.draft_text,
        );
        if (withDraft) setDraftPayload(withDraft.draft_payload as DraftPayload);
      })
      .catch(() => {});
  }, [token, approval?.workflow_id, orgSlug]);

  const handleApprove = async () => {
    if (!approval || !token) return;
    const approveToken = tokenParam || approval.token_hash || "";
    setActionLoading("approve");
    try {
      await approveWorkflowToken(token, approval.workflow_id, approveToken, orgSlug);
      setApproved(true);
      setApproval((prev) => (prev ? { ...prev, status: "APPROVED" } : prev));
      setMessage(null);
    } catch (err: any) {
      setMessage(`Approval failed: ${err.message}`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async () => {
    if (!approval || !token) return;
    if (rejectReason.trim().length < 20) {
      setMessage("Rejection reason must be at least 20 characters.");
      return;
    }
    const approveToken = tokenParam || approval.token_hash || "";
    setActionLoading("reject");
    try {
      await rejectWorkflowToken(
        token,
        approval.workflow_id,
        approveToken,
        rejectReason.trim(),
        orgSlug,
      );
      setApproval((prev) => (prev ? { ...prev, status: "REJECTED" } : prev));
      setShowRejectInput(false);
      setMessage(null);
    } catch (err: any) {
      setMessage(`Rejection failed: ${err.message}`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleDownload = () => {
    if (!draftPayload?.draft_text) return;
    const title = approval?.description || "Legal Notice";
    const citations = draftPayload.source_citations || [];

    const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>${title}</title>
  <style>
    body { font-family: 'Times New Roman', serif; max-width: 750px; margin: 60px auto; line-height: 1.8; color: #111; }
    h1 { font-size: 18px; text-align: center; margin-bottom: 40px; }
    .content { white-space: pre-wrap; font-size: 13px; }
    .sources { margin-top: 40px; border-top: 1px solid #ccc; padding-top: 20px; font-size: 11px; color: #555; }
    .sources h2 { font-size: 13px; }
  </style>
</head>
<body>
  <h1>${title}</h1>
  <div class="content">${draftPayload.draft_text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
  ${citations.length > 0 ? `
  <div class="sources">
    <h2>Source Citations</h2>
    <ol>${citations.map((c) => `<li>${c}</li>`).join("")}</ol>
  </div>` : ""}
</body>
</html>`;

    const blob = new Blob([html], { type: "application/msword" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^a-z0-9]/gi, "_").toLowerCase()}.doc`;
    a.click();
    URL.revokeObjectURL(url);
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

        {/* Header */}
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

        {/* Draft Preview (always visible if available) */}
        {draftPayload?.draft_text && !draftPayload.grounding_failed && (
          <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-blue-400" />
                <h2 className="text-white font-semibold text-sm">Draft Notice</h2>
                {draftPayload.grounding_score !== undefined && (
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    draftPayload.grounding_score >= 0.7
                      ? "bg-emerald-500/20 text-emerald-400"
                      : "bg-amber-500/20 text-amber-400"
                  }`}>
                    {(draftPayload.grounding_score * 100).toFixed(0)}% grounded
                  </span>
                )}
              </div>
              <button
                onClick={handleDownload}
                className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
                Download .DOC
              </button>
            </div>

            {draftPayload.qa?.has_issues && (
              <div className="mb-4 p-3 bg-amber-900/20 border border-amber-500/30 rounded-lg text-amber-400 text-xs">
                ⚠️ QA note: {draftPayload.qa.summary}
              </div>
            )}

            <pre className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap font-serif max-h-96 overflow-y-auto">
              {draftPayload.draft_text}
            </pre>

            {draftPayload.missing_info && draftPayload.missing_info.length > 0 && (
              <div className="mt-4 pt-4 border-t border-slate-700">
                <p className="text-xs text-slate-500 font-medium mb-1">Information gaps noted by Lex:</p>
                <ul className="list-disc list-inside space-y-0.5">
                  {draftPayload.missing_info.map((info, i) => (
                    <li key={i} className="text-xs text-slate-500">{info}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Post-approval state */}
        {approved && (
          <div className="bg-emerald-900/20 border border-emerald-500/30 rounded-lg p-6 mb-6">
            <div className="flex items-center gap-3 mb-3">
              <CheckCircle className="w-5 h-5 text-emerald-400" />
              <h2 className="text-emerald-400 font-semibold">Action Approved — Executing</h2>
            </div>
            <p className="text-slate-400 text-sm mb-4">
              The workflow is now executing. In the meantime, download your
              notice below and send it from your email client to the relevant party.
            </p>
            {draftPayload?.draft_text && (
              <button
                onClick={handleDownload}
                className="inline-flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg transition-colors"
              >
                <Download className="w-4 h-4" />
                Download Final Notice (.DOC)
              </button>
            )}
          </div>
        )}

        {/* Rejected state */}
        {approval.status === "REJECTED" && !approved && (
          <div className="bg-red-900/20 border border-red-500/30 rounded-lg p-4 mb-6">
            <p className="text-red-400 text-sm font-medium">This action was rejected.</p>
            <p className="text-slate-500 text-xs mt-1">
              The workflow has been marked as rejected. No notice was sent.
            </p>
          </div>
        )}

        {/* Error message */}
        {message && (
          <div className="p-4 rounded-lg mb-4 text-sm bg-red-900/30 text-red-400">
            {message}
          </div>
        )}

        {/* Actions — only show if PENDING */}
        {approval.status === "PENDING" && !approved && (
          <div className="space-y-4">
            <div className="flex gap-4">
              <button
                onClick={handleApprove}
                disabled={actionLoading !== null}
                className="flex-1 px-6 py-3 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-700 text-white font-semibold rounded-lg transition-colors"
              >
                {actionLoading === "approve" ? "Processing…" : "✅ Approve & Execute"}
              </button>
              <button
                onClick={() => setShowRejectInput((v) => !v)}
                disabled={actionLoading !== null}
                className="flex-1 px-6 py-3 bg-red-600 hover:bg-red-700 disabled:bg-slate-700 text-white font-semibold rounded-lg transition-colors"
              >
                ❌ Reject
              </button>
            </div>
            {showRejectInput && (
              <div className="space-y-2">
                <textarea
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Reason for rejection (minimum 20 characters)…"
                  rows={3}
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white text-sm placeholder-slate-500 focus:outline-none focus:border-red-500 resize-none"
                />
                <div className="flex items-center justify-between">
                  <span className={`text-xs ${
                    rejectReason.trim().length >= 20 ? "text-slate-500" : "text-red-400"
                  }`}>
                    {rejectReason.trim().length}/20 min characters
                  </span>
                  <button
                    onClick={handleReject}
                    disabled={actionLoading !== null || rejectReason.trim().length < 20}
                    className="px-4 py-2 bg-red-700 hover:bg-red-800 disabled:bg-slate-700 disabled:text-slate-500 text-white text-sm font-semibold rounded-lg transition-colors"
                  >
                    {actionLoading === "reject" ? "Rejecting…" : "Confirm Rejection"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
