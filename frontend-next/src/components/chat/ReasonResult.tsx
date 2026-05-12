"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Scale,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  FileText,
  CheckCircle,
  XCircle,
} from "lucide-react";

interface Citation {
  document_name?: string;
  clause_reference?: string;
  exact_text?: string;
}

interface FindingData {
  id: string;
  claim: string;
  confidence: number;
  supporting_citations?: Citation[];
  reference_chain?: any;
  definitional_conflicts?: any;
  escalated: boolean;
  escalation_type?: string | null;
}

interface ReasonResultProps {
  answer?: string | null;
  findings?: FindingData[];
  logs?: {
    id: string;
    tool_name: string;
    status: string;
    summary: string;
    created_at: string;
  }[];
}

export function ReasonResult({ answer, findings, logs }: ReasonResultProps) {
  const [expandedFindings, setExpandedFindings] = useState<Set<string>>(
    new Set(),
  );
  const [expandedLogs, setExpandedLogs] = useState<Set<string>>(new Set());

  const toggleFinding = (id: string) => {
    setExpandedFindings((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleLog = (id: string) => {
    setExpandedLogs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const confidenceColor = (score: number) => {
    if (score >= 0.8) return "text-emerald-400 bg-emerald-500/10";
    if (score >= 0.5) return "text-amber-400 bg-amber-500/10";
    return "text-red-400 bg-red-500/10";
  };

  const statusColor = (status: string) => {
    switch (status) {
      case "SUCCESS":
        return "text-emerald-400 bg-emerald-500/10";
      case "FAILED":
        return "text-red-400 bg-red-500/10";
      default:
        return "text-slate-400 bg-slate-500/10";
    }
  };

  return (
    <div className="space-y-3 mx-4 my-2">
      {/* LLM Answer */}
      {answer && (
        <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Scale className="w-5 h-5 text-accent-gold" />
            <h3 className="text-white font-semibold">Reasoning Results</h3>
          </div>
          <div className="prose prose-invert prose-sm max-w-none text-slate-300">
            <ReactMarkdown>{answer}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Structured Findings Cards */}
      {findings && findings.length > 0 && (
        <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-4">
          <h4 className="text-sm font-semibold text-slate-300 mb-3">
            Findings ({findings.length})
          </h4>
          <div className="space-y-2">
            {findings.map((finding) => (
              <div
                key={finding.id}
                className="border border-slate-800 rounded-lg overflow-hidden"
              >
                <button
                  onClick={() => toggleFinding(finding.id)}
                  className="w-full flex items-start gap-3 p-3 hover:bg-slate-800/50 transition-colors text-left"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white font-medium">
                      {finding.claim}
                    </p>
                    <div className="flex items-center gap-2 mt-1">
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded font-medium ${confidenceColor(finding.confidence)}`}
                      >
                        {(finding.confidence * 100).toFixed(0)}% confidence
                      </span>
                      {finding.escalated && (
                        <span className="text-xs px-1.5 py-0.5 rounded font-medium text-red-400 bg-red-500/10 flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3" />
                          {finding.escalation_type || "ESCALATED"}
                        </span>
                      )}
                    </div>
                  </div>
                  {expandedFindings.has(finding.id) ? (
                    <ChevronDown className="w-4 h-4 text-slate-400 mt-1 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-slate-400 mt-1 flex-shrink-0" />
                  )}
                </button>

                {expandedFindings.has(finding.id) && (
                  <div className="px-3 pb-3 space-y-2">
                    {/* Source Citations */}
                    {finding.supporting_citations &&
                      finding.supporting_citations.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-slate-400 mb-1">
                            📎 Source Clauses
                          </p>
                          {finding.supporting_citations.map((cit, i) => (
                            <div
                              key={i}
                              className="bg-slate-950 rounded p-2 mb-1"
                            >
                              <p className="text-xs text-slate-300">
                                <span className="text-accent-blue">
                                  {cit.document_name || "Document"}
                                </span>
                                {cit.clause_reference && (
                                  <span className="text-slate-500">
                                    {" "}
                                    — {cit.clause_reference}
                                  </span>
                                )}
                              </p>
                              {cit.exact_text && (
                                <p className="text-xs text-slate-500 mt-0.5 italic">
                                  &quot;{cit.exact_text.substring(0, 200)}
                                  {cit.exact_text.length > 200 ? "..." : ""}
                                  &quot;
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                    {/* Reference Chain (graph-expanded) */}
                    {finding.reference_chain && (
                      <div>
                        <p className="text-xs font-medium text-slate-400 mb-1">
                          🔗 Reference Chain
                        </p>
                        <pre className="text-xs text-slate-500 bg-slate-950 rounded p-2 overflow-x-auto">
                          {JSON.stringify(finding.reference_chain, null, 2)}
                        </pre>
                      </div>
                    )}

                    {/* Definitional Conflicts */}
                    {finding.definitional_conflicts &&
                      finding.definitional_conflicts.length > 0 && (
                        <div className="p-2 bg-red-950/30 border border-red-800/30 rounded">
                          <p className="text-xs font-medium text-red-400 mb-1">
                            ⚠ Definitional Conflict
                          </p>
                          <pre className="text-xs text-red-300/70 overflow-x-auto">
                            {JSON.stringify(
                              finding.definitional_conflicts,
                              null,
                              2,
                            )}
                          </pre>
                        </div>
                      )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Execution Logs */}
      {logs && logs.length > 0 && (
        <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-4">
          <h4 className="text-sm font-semibold text-slate-300 mb-3">
            Execution Logs ({logs.length})
          </h4>
          <div className="space-y-2">
            {logs.map((log) => (
              <div
                key={log.id}
                className="border border-slate-800 rounded-lg overflow-hidden"
              >
                <button
                  onClick={() => toggleLog(log.id)}
                  className="w-full flex items-center justify-between p-3 hover:bg-slate-800/50 transition-colors text-left"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <FileText className="w-4 h-4 text-slate-400 flex-shrink-0" />
                    <span className="text-sm text-slate-200 truncate">
                      {log.tool_name}
                    </span>
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded font-medium ${statusColor(log.status)}`}
                    >
                      {log.status}
                    </span>
                  </div>
                  {expandedLogs.has(log.id) ? (
                    <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
                  )}
                </button>
                {expandedLogs.has(log.id) && log.summary && (
                  <div className="px-3 pb-3">
                    <p className="text-xs text-slate-400 whitespace-pre-wrap">
                      {log.summary}
                    </p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
