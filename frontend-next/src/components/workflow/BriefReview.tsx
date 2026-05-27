"use client";

import { useState } from "react";
import {
  AlertTriangle, CheckSquare, ChevronDown, ChevronUp,
  FileText, Scale, Sparkles, Clock,
} from "lucide-react";
import { clsx } from "clsx";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import type { DecisionBrief } from "@/lib/types";

interface BriefReviewProps {
  brief: DecisionBrief;
  workflowId: string;
  onProceed: () => Promise<void>;
  onAbort: () => Promise<void>;
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "#3ECFA4" : pct >= 60 ? "#E8A44C" : "#F06B6B";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[#2A2A32] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="text-xs font-mono" style={{ color }}>{pct}%</span>
    </div>
  );
}

export function BriefReview({ brief, workflowId, onProceed, onAbort }: BriefReviewProps) {
  const [proceeding, setProceeding] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [checklistDone, setChecklistDone] = useState<Set<number>>(new Set());
  const [clausesExpanded, setClausesExpanded] = useState(true);

  const handleProceed = async () => {
    setProceeding(true);
    try { await onProceed(); } finally { setProceeding(false); }
  };

  const handleAbort = async () => {
    setAborting(true);
    try { await onAbort(); } finally { setAborting(false); }
  };

  const toggleCheck = (i: number) =>
    setChecklistDone((prev) => {
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
            <Scale className="w-4 h-4 text-[#D4A853]" strokeWidth={1.5} />
            <span className="text-xs font-medium text-[#D4A853] uppercase tracking-wider">
              Decision Brief
            </span>
          </div>
          <h2 className="text-xl font-semibold text-[#F0EEE9]">Review Before Drafting</h2>
          <p className="text-sm text-[#7A7A8A] mt-1">
            Confirm the analysis below before the AI generates the draft.
          </p>
        </div>
        <Badge variant={brief.proceed_recommended ? "success" : "warning"}>
          {brief.proceed_recommended ? "Proceed Recommended" : "Review Required"}
        </Badge>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-5">

        {/* LEFT COLUMN */}
        <div className="space-y-4">

          {/* Summary */}
          <Card className="p-5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-[#7C6AF7]" strokeWidth={1.5} />
              <h3 className="text-sm font-semibold text-[#F0EEE9]">Summary</h3>
            </div>
            <p className="text-sm text-[#F0EEE9] leading-relaxed">{brief.summary}</p>

            <div className="mt-4 pt-4 border-t border-[#2A2A32]">
              <p className="text-xs text-[#7A7A8A] mb-1.5">Analysis confidence</p>
              <ConfidenceBar value={brief.confidence} />
            </div>

            {brief.proceed_reasoning && (
              <p className="mt-3 text-xs text-[#7A7A8A] italic border-l-2 border-[#2A2A32] pl-3">
                {brief.proceed_reasoning}
              </p>
            )}
          </Card>

          {/* Relevant Clauses */}
          {brief.relevant_clauses.length > 0 && (
            <Card className="overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-5 py-4 hover:bg-[#1E1E28] transition-colors"
                onClick={() => setClausesExpanded((v) => !v)}
              >
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-[#7A7A8A]" strokeWidth={1.5} />
                  <span className="text-sm font-semibold text-[#F0EEE9]">Relevant Clauses</span>
                  <Badge variant="muted">{brief.relevant_clauses.length}</Badge>
                </div>
                {clausesExpanded
                  ? <ChevronUp className="w-4 h-4 text-[#7A7A8A]" />
                  : <ChevronDown className="w-4 h-4 text-[#7A7A8A]" />}
              </button>

              {clausesExpanded && (
                <div className="px-5 pb-5 space-y-3">
                  {brief.relevant_clauses.map((clause, i) => (
                    <div key={i} className="p-4 rounded-lg bg-[#1E1E28] border border-[#2A2A32]">
                      <div className="flex items-center gap-2 mb-2">
                        <Badge variant="muted">{clause.clause_ref}</Badge>
                      </div>
                      <blockquote className="text-sm text-[#F0EEE9] leading-relaxed font-serif italic border-l-2 border-[#D4A853]/40 pl-3 mb-2">
                        "{clause.excerpt}"
                      </blockquote>
                      <p className="text-xs text-[#7A7A8A]">
                        <span className="text-[#D4A853]">Why this matters: </span>
                        {clause.relevance_reason}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}

          {/* Conflicts */}
          {brief.conflicts_to_resolve.length > 0 && (
            <Card className="p-5 border-[#F06B6B]/20 bg-[#F06B6B]/[0.03]">
              <div className="flex items-center gap-2 mb-3">
                <AlertTriangle className="w-4 h-4 text-[#F06B6B]" strokeWidth={1.5} />
                <h3 className="text-sm font-semibold text-[#F06B6B]">
                  Conflicts to Resolve
                </h3>
                <Badge variant="danger">{brief.conflicts_to_resolve.length}</Badge>
              </div>
              <div className="space-y-3">
                {brief.conflicts_to_resolve.map((c, i) => (
                  <div key={i} className="p-3 rounded-lg bg-[#F06B6B]/5 border border-[#F06B6B]/15">
                    <p className="text-sm font-medium text-[#F06B6B] mb-1">{c.type}</p>
                    <p className="text-xs text-[#F0EEE9]/70">{c.description}</p>
                    <div className="flex gap-2 mt-2 flex-wrap">
                      <Badge variant="danger" className="text-[10px]">{c.source_a}</Badge>
                      {c.source_b && <Badge variant="danger" className="text-[10px]">{c.source_b}</Badge>}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Deadlines */}
          {brief.deadlines_implicated.length > 0 && (
            <Card className="p-5 border-[#E8A44C]/20 bg-[#E8A44C]/[0.03]">
              <div className="flex items-center gap-2 mb-3">
                <Clock className="w-4 h-4 text-[#E8A44C]" strokeWidth={1.5} />
                <h3 className="text-sm font-semibold text-[#E8A44C]">Deadlines Implicated</h3>
              </div>
              <ul className="space-y-1">
                {brief.deadlines_implicated.map((d, i) => (
                  <li key={i} className="text-sm text-[#F0EEE9]/80 flex items-start gap-2">
                    <span className="text-[#E8A44C] mt-0.5">•</span>
                    {d}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        {/* RIGHT COLUMN */}
        <div className="space-y-4">

          {/* Recommended Actions */}
          <Card className="p-5" gold>
            <h3 className="text-sm font-semibold text-[#F0EEE9] mb-3">Recommended Actions</h3>
            <div className="space-y-3">
              {brief.recommended_actions.map((action, i) => {
                const urgencyColor =
                  action.urgency >= 0.8 ? "#F06B6B" :
                  action.urgency >= 0.6 ? "#E8A44C" : "#3ECFA4";
                return (
                  <div key={i} className="p-3 rounded-lg bg-[#1E1E28] border border-[#2A2A32]">
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <Badge variant="gold" className="text-[10px]">
                        {action.action_type.replace("DRAFT_", "")}
                      </Badge>
                      <span
                        className="text-[10px] font-mono font-semibold"
                        style={{ color: urgencyColor }}
                      >
                        {Math.round(action.urgency * 100)}%
                      </span>
                    </div>
                    <p className="text-xs text-[#F0EEE9] leading-relaxed">{action.description}</p>
                    {action.deadline_date && (
                      <p className="text-[10px] text-[#E8A44C] mt-1.5 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        Due: {new Date(action.deadline_date).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Verification Checklist */}
          {brief.verification_checklist.length > 0 && (
            <Card className="p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-[#F0EEE9]">Pre-Draft Checklist</h3>
                <span className="text-xs text-[#7A7A8A]">
                  {checklistDone.size}/{brief.verification_checklist.length}
                </span>
              </div>
              <div className="space-y-2">
                {brief.verification_checklist.map((item, i) => (
                  <button
                    key={i}
                    onClick={() => toggleCheck(i)}
                    className="w-full flex items-start gap-2.5 text-left p-2 rounded-lg hover:bg-[#1E1E28] transition-colors"
                  >
                    <CheckSquare
                      className={clsx(
                        "w-4 h-4 mt-0.5 shrink-0 transition-colors",
                        checklistDone.has(i) ? "text-[#3ECFA4]" : "text-[#2A2A32]",
                      )}
                    />
                    <span
                      className={clsx(
                        "text-xs transition-colors",
                        checklistDone.has(i)
                          ? "text-[#7A7A8A] line-through"
                          : "text-[#F0EEE9]",
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
          <div className="space-y-2 pt-1">
            <Button
              variant="gold"
              size="lg"
              className="w-full"
              loading={proceeding}
              onClick={handleProceed}
            >
              Proceed to Draft →
            </Button>
            <Button
              variant="ghost"
              size="lg"
              className="w-full"
              loading={aborting}
              onClick={handleAbort}
            >
              Abort
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
