"use client";

import { useState } from "react";
import {
  Download, CheckSquare, AlertCircle, ChevronDown,
  ChevronUp, FileCheck, Link as LinkIcon, ThumbsDown,
} from "lucide-react";
import { clsx } from "clsx";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import type { DraftPayload } from "@/lib/types";

interface DraftReviewProps {
  draft: DraftPayload;
  workflowId: string;
  r2Key?: string | null;
  onApprove: () => Promise<void>;
  onReject: (reason: string) => Promise<void>;
}

export function DraftReview({
  draft, workflowId, r2Key, onApprove, onReject,
}: DraftReviewProps) {
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [checkDone, setCheckDone] = useState<Set<number>>(new Set());
  const [citExpanded, setCitExpanded] = useState(false);

  const groundingPct = Math.round((draft.grounding_score ?? 0) * 100);
  const groundingColor =
    groundingPct >= 80 ? "#3ECFA4" : groundingPct >= 60 ? "#E8A44C" : "#F06B6B";

  const handleApprove = async () => {
    setApproving(true);
    try { await onApprove(); } finally { setApproving(false); }
  };

  const handleReject = async () => {
    if (rejectReason.trim().length < 20) return;
    setRejecting(true);
    try { await onReject(rejectReason); } finally { setRejecting(false); }
  };

  const toggleCheck = (i: number) =>
    setCheckDone((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <FileCheck className="w-4 h-4 text-[#7C6AF7]" strokeWidth={1.5} />
            <span className="text-xs font-medium text-[#7C6AF7] uppercase tracking-wider">
              Draft Review
            </span>
          </div>
          <h2 className="text-xl font-semibold text-[#F0EEE9]">Review the Draft</h2>
          <p className="text-sm text-[#7A7A8A] mt-1">
            Approve to export as DOCX, or reject with feedback to revise.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#7A7A8A]">Grounding</span>
          <span className="text-sm font-semibold font-mono" style={{ color: groundingColor }}>
            {groundingPct}%
          </span>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-5">

        {/* LEFT — Draft text */}
        <div className="space-y-4">
          <Card className="p-5 border-[#7C6AF7]/15">
            <div className="prose prose-sm max-w-none">
              <div className="legal-text whitespace-pre-wrap text-[#F0EEE9] leading-[1.85] text-[15px]">
                {draft.draft_text}
              </div>
            </div>
          </Card>

          {/* Missing info warnings */}
          {draft.missing_info && draft.missing_info.length > 0 && (
            <Card className="p-5 border-[#E8A44C]/20 bg-[#E8A44C]/[0.03]">
              <div className="flex items-center gap-2 mb-3">
                <AlertCircle className="w-4 h-4 text-[#E8A44C]" strokeWidth={1.5} />
                <h3 className="text-sm font-semibold text-[#E8A44C]">Missing Information</h3>
              </div>
              <ul className="space-y-1.5">
                {draft.missing_info.map((item, i) => (
                  <li key={i} className="text-xs text-[#F0EEE9]/80 flex items-start gap-2">
                    <span className="text-[#E8A44C] mt-0.5 shrink-0">!</span>
                    {item}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Citations */}
          {draft.source_citations && draft.source_citations.length > 0 && (
            <Card className="overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-[#1E1E28] transition-colors"
                onClick={() => setCitExpanded((v) => !v)}
              >
                <div className="flex items-center gap-2">
                  <LinkIcon className="w-4 h-4 text-[#7A7A8A]" strokeWidth={1.5} />
                  <span className="text-sm font-semibold text-[#F0EEE9]">Source Citations</span>
                  <Badge variant="violet">{draft.source_citations.length}</Badge>
                </div>
                {citExpanded ? <ChevronUp className="w-4 h-4 text-[#7A7A8A]" /> : <ChevronDown className="w-4 h-4 text-[#7A7A8A]" />}
              </button>
              {citExpanded && (
                <div className="px-5 pb-4 space-y-2">
                  {draft.source_citations.map((c, i) => (
                    <div key={i} className="flex items-start gap-2.5 text-xs text-[#7A7A8A]">
                      <span className="text-[#7C6AF7] font-mono shrink-0">[{i + 1}]</span>
                      <span>{c}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </div>

        {/* RIGHT COLUMN */}
        <div className="space-y-4">

          {/* Grounding Score */}
          <Card className="p-5">
            <p className="text-xs text-[#7A7A8A] mb-2">Grounding score</p>
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-[#2A2A32] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${groundingPct}%`, background: groundingColor }}
                />
              </div>
              <span
                className="text-sm font-bold font-mono w-10 text-right"
                style={{ color: groundingColor }}
              >
                {groundingPct}%
              </span>
            </div>
            <p className="text-[11px] text-[#7A7A8A] mt-2">
              {groundingPct >= 80
                ? "High confidence — all claims are cited to source material."
                : groundingPct >= 60
                ? "Moderate confidence — review flagged sections carefully."
                : "Low confidence — significant claims may not be grounded."}
            </p>
          </Card>

          {/* Verification Checklist */}
          {draft.verification_checklist && draft.verification_checklist.length > 0 && (
            <Card className="p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-[#F0EEE9]">Review Checklist</h3>
                <span className="text-xs text-[#7A7A8A]">
                  {checkDone.size}/{draft.verification_checklist.length}
                </span>
              </div>
              <div className="space-y-2">
                {draft.verification_checklist.map((item, i) => (
                  <button
                    key={i}
                    onClick={() => toggleCheck(i)}
                    className="w-full flex items-start gap-2.5 text-left p-2 rounded-lg hover:bg-[#1E1E28] transition-colors"
                  >
                    <CheckSquare
                      className={clsx(
                        "w-4 h-4 mt-0.5 shrink-0 transition-colors",
                        checkDone.has(i) ? "text-[#3ECFA4]" : "text-[#2A2A32]",
                      )}
                    />
                    <span
                      className={clsx(
                        "text-xs transition-colors",
                        checkDone.has(i) ? "text-[#7A7A8A] line-through" : "text-[#F0EEE9]",
                      )}
                    >
                      {item}
                    </span>
                  </button>
                ))}
              </div>
            </Card>
          )}

          {/* CTAs */}
          <div className="space-y-2">
            <Button
              variant="gold"
              size="lg"
              className="w-full"
              loading={approving}
              onClick={handleApprove}
            >
              <Download className="w-4 h-4" />
              Approve & Export DOCX
            </Button>

            <Button
              variant="danger"
              size="lg"
              className="w-full"
              onClick={() => setShowReject((v) => !v)}
            >
              <ThumbsDown className="w-4 h-4" />
              Reject Draft
            </Button>

            {showReject && (
              <div className="space-y-2 animate-slide-up">
                <textarea
                  className="w-full bg-[#1E1E28] border border-[#2A2A32] focus:border-[#F06B6B] rounded-lg px-3 py-2 text-sm text-[#F0EEE9] placeholder-[#4A4A5A] resize-none outline-none transition-colors"
                  rows={3}
                  placeholder="Describe what needs to change (min 20 chars)…"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  maxLength={500}
                />
                <Button
                  variant="danger"
                  size="md"
                  className="w-full"
                  disabled={rejectReason.trim().length < 20}
                  loading={rejecting}
                  onClick={handleReject}
                >
                  Submit Rejection
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
