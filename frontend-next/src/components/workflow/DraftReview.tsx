"use client";

import { useState } from "react";
import {
  Download, CheckSquare, AlertCircle, ChevronDown,
  ChevronUp, FileCheck, Link as LinkIcon, ThumbsDown, Edit3,
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
  onApprove: (updatedDraft: string, resolvedMissing: Record<string, string>) => Promise<void>;
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

  // Live editing state
  const [editedDraft, setEditedDraft] = useState(draft.draft_text);
  const [isEditing, setIsEditing] = useState(false);

  // Missing info fill-in state (keyed by the missing_info string)
  const [resolvedMissing, setResolvedMissing] = useState<Record<string, string>>({});

  const groundingPct = Math.round((draft.grounding_score ?? 0) * 100);
  const groundingColor =
    groundingPct >= 80 ? "#3ECFA4" : groundingPct >= 60 ? "#E8A44C" : "#F06B6B";

  const allMissingResolved =
    !draft.missing_info ||
    draft.missing_info.length === 0 ||
    draft.missing_info.every((item) => resolvedMissing[item]?.trim());

  const handleApprove = async () => {
    setApproving(true);
    try {
      await onApprove(editedDraft, resolvedMissing);
    } finally {
      setApproving(false);
    }
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
          <h2 className="text-xl font-semibold text-[#F0EEE9]">Review & Edit the Draft</h2>
          <p className="text-sm text-[#7A7A8A] mt-1">
            Edit the draft directly, fill in any missing information, then approve to export as DOCX.
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

        {/* LEFT — Draft text (editable) */}
        <div className="space-y-4">
          <Card className="p-5 border-[#7C6AF7]/15">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-[#7A7A8A] uppercase tracking-wider">
                Draft Document
              </span>
              <button
                onClick={() => setIsEditing((v) => !v)}
                className="flex items-center gap-1.5 text-xs text-[#7C6AF7] hover:text-[#9A8AFF] transition-colors"
              >
                <Edit3 className="w-3.5 h-3.5" />
                {isEditing ? "Done editing" : "Edit draft"}
              </button>
            </div>
            {isEditing ? (
              <textarea
                className="w-full bg-[#1A1A22] border border-[#7C6AF7]/30 focus:border-[#7C6AF7] rounded-lg px-4 py-3 text-[15px] text-[#F0EEE9] leading-[1.85] outline-none transition-colors resize-y font-mono"
                rows={20}
                value={editedDraft}
                onChange={(e) => setEditedDraft(e.target.value)}
                spellCheck={false}
              />
            ) : (
              <div className="legal-text whitespace-pre-wrap text-[#F0EEE9] leading-[1.85] text-[15px] min-h-[200px]">
                {editedDraft}
              </div>
            )}
            {editedDraft !== draft.draft_text && (
              <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[#7C6AF7]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#7C6AF7]" />
                Draft edited — changes will be saved to the exported DOCX
              </div>
            )}
          </Card>

          {/* Missing info — now fill-in inputs */}
          {draft.missing_info && draft.missing_info.length > 0 && (
            <Card className="p-5 border-[#E8A44C]/20 bg-[#E8A44C]/[0.03]">
              <div className="flex items-center gap-2 mb-3">
                <AlertCircle className="w-4 h-4 text-[#E8A44C]" strokeWidth={1.5} />
                <h3 className="text-sm font-semibold text-[#E8A44C]">
                  Missing Information — Please Fill In
                </h3>
              </div>
              <p className="text-xs text-[#7A7A8A] mb-4">
                The AI flagged these gaps. Fill them in here and they will be appended to the exported document.
              </p>
              <div className="space-y-3">
                {draft.missing_info.map((item, i) => {
                  const resolved = resolvedMissing[item];
                  return (
                    <div key={i} className="space-y-1">
                      <label className="flex items-start gap-2 text-xs text-[#F0EEE9]/80">
                        <span
                          className={clsx(
                            "mt-0.5 shrink-0 font-bold",
                            resolved?.trim() ? "text-[#3ECFA4]" : "text-[#E8A44C]",
                          )}
                        >
                          {resolved?.trim() ? "✓" : "!"}
                        </span>
                        <span className={clsx(resolved?.trim() && "line-through text-[#7A7A8A]")}>
                          {item}
                        </span>
                      </label>
                      <input
                        type="text"
                        className={clsx(
                          "w-full bg-[#1E1E28] border rounded-lg px-3 py-2 text-sm text-[#F0EEE9] placeholder-[#4A4A5A] outline-none transition-colors",
                          resolved?.trim()
                            ? "border-[#3ECFA4]/30 focus:border-[#3ECFA4]"
                            : "border-[#2A2A32] focus:border-[#E8A44C]",
                        )}
                        placeholder="Provide your answer here…"
                        value={resolved ?? ""}
                        onChange={(e) =>
                          setResolvedMissing((prev) => ({ ...prev, [item]: e.target.value }))
                        }
                      />
                    </div>
                  );
                })}
              </div>
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

          {/* Missing info status */}
          {draft.missing_info && draft.missing_info.length > 0 && (
            <Card className="p-4">
              <p className="text-xs text-[#7A7A8A] mb-2">Missing info status</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-[#2A2A32] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[#3ECFA4] transition-all duration-500"
                    style={{
                      width: `${Math.round(
                        (Object.values(resolvedMissing).filter((v) => v?.trim()).length /
                          draft.missing_info.length) *
                          100,
                      )}%`,
                    }}
                  />
                </div>
                <span className="text-xs text-[#7A7A8A] font-mono">
                  {Object.values(resolvedMissing).filter((v) => v?.trim()).length}/
                  {draft.missing_info.length} filled
                </span>
              </div>
            </Card>
          )}

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
            {draft.missing_info && draft.missing_info.length > 0 && !allMissingResolved && (
              <p className="text-[11px] text-[#E8A44C] text-center px-1">
                Fill in all missing information above before approving.
              </p>
            )}
            <Button
              variant="gold"
              size="lg"
              className="w-full"
              loading={approving}
              onClick={handleApprove}
            >
              <Download className="w-4 h-4" />
              Approve &amp; Export DOCX
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
