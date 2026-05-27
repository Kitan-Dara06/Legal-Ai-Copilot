"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

interface ReasonResultProps {
  result: {
    findings_summary?: string;
    definitional_conflicts?: Array<{
      term: string;
      definition: string;
      source_document_id: string;
      conflict_description: string | null;
    }>;
    escalations?: Array<{
      type: string;
      description: string;
      term?: string;
    }>;
    graph_degraded?: boolean;
  };
}

export function ReasonResult({ result }: ReasonResultProps) {
  const [expandedFindings, setExpandedFindings] = useState(false);

  const hasConflicts =
    result.definitional_conflicts && result.definitional_conflicts.length > 0;
  const hasEscalations = result.escalations && result.escalations.length > 0;

  const escalationColor = (
    type: string,
  ): "success" | "warning" | "danger" | "violet" | "default" => {
    switch (type) {
      case "DEFINITIONAL_CONFLICT":
        return "warning";
      case "STRUCTURAL_AMBIGUITY":
        return "warning";
      case "DEGRADED":
        return "warning";
      case "INSUFFICIENT_COVERAGE":
        return "warning";
      default:
        return "default";
    }
  };

  return (
    <div className="space-y-4 mb-6">
      {/* Warning Banner for pre-ingestion conflicts */}
      {hasConflicts && (
        <Card className="p-4 border-yellow-500/30">
          <div className="flex items-center gap-3">
            <span className="text-yellow-400 text-lg">⚠</span>
            <div>
              <h3 className="text-yellow-400 font-semibold text-sm">
                Definitional Conflicts Detected
              </h3>
              <p className="text-slate-400 text-xs mt-1">
                Some terms in the context have conflicting definitions across
                documents. Review the findings carefully.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Degraded Warning */}
      {result.graph_degraded && (
        <Card className="p-4 border-orange-500/30">
          <div className="flex items-center gap-3">
            <span className="text-orange-400 text-lg">⚡</span>
            <div>
              <h3 className="text-orange-400 font-semibold text-sm">
                Degraded Mode
              </h3>
              <p className="text-slate-400 text-xs mt-1">
                Cross-reference graph was unavailable. Results are
                retrieval-only.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Findings Card */}
      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-white font-semibold text-lg">Findings</h3>
          {hasEscalations && (
            <Badge variant="danger">
              {result.escalations!.length} escalations
            </Badge>
          )}
        </div>

        <div className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
          {result.findings_summary || "Analysis complete."}
        </div>
      </Card>

      {/* Definitional Conflicts */}
      {hasConflicts && (
        <Card className="p-4">
          <h3 className="text-white font-semibold mb-3">
            Definitional Conflicts
          </h3>
          <div className="space-y-2">
            {result.definitional_conflicts!.map((dc, i) => (
              <div key={i} className="p-3 bg-slate-800/50 rounded-lg">
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant="warning">Conflict</Badge>
                  <span className="text-white text-sm font-medium">
                    {dc.term}
                  </span>
                </div>
                <p className="text-slate-400 text-xs">
                  {dc.conflict_description ||
                    `Defined in document ${dc.source_document_id}`}
                </p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Escalations */}
      {hasEscalations && (
        <Card className="p-4">
          <h3 className="text-white font-semibold mb-3">Escalations</h3>
          <div className="space-y-2">
            {result.escalations!.map((esc, i) => (
              <div key={i} className="p-3 bg-slate-800/50 rounded-lg">
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant={escalationColor(esc.type)}>{esc.type}</Badge>
                  {esc.term && (
                    <span className="text-slate-300 text-sm">{esc.term}</span>
                  )}
                </div>
                <p className="text-slate-400 text-xs">{esc.description}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
