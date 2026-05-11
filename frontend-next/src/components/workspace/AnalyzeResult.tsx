"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

interface AnalyzeResultProps {
  result: {
    answer: string;
    confidence: number;
    source_citations: string[];
    faithfulness_score: number;
  } | null;
}

export function AnalyzeResult({ result }: AnalyzeResultProps) {
  const [expandedSources, setExpandedSources] = useState(false);

  if (!result) {
    return (
      <Card className="mb-6 p-4 border-yellow-500/30">
        <div className="flex items-center gap-3">
          <span className="text-yellow-400 text-lg">⚠</span>
          <div>
            <h3 className="text-yellow-400 font-semibold">Undetermined</h3>
            <p className="text-slate-400 text-sm">
              The system could not find enough relevant evidence in the
              documents to answer confidently. Consider rephrasing your question
              or uploading additional documents.
            </p>
          </div>
        </div>
      </Card>
    );
  }

  const confidenceColor =
    result.confidence >= 0.8
      ? "success"
      : result.confidence >= 0.5
        ? "warning"
        : "error";
  const faithfulnessColor =
    result.faithfulness_score >= 0.8
      ? "success"
      : result.faithfulness_score >= 0.5
        ? "warning"
        : "error";

  return (
    <Card className="mb-6 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-white font-semibold text-lg">Analysis Result</h3>
        <div className="flex items-center gap-2">
          <Badge variant={confidenceColor}>
            {(result.confidence * 100).toFixed(0)}% confidence
          </Badge>
          <Badge variant={faithfulnessColor}>
            Faithfulness: {(result.faithfulness_score * 100).toFixed(0)}%
          </Badge>
        </div>
      </div>

      <div className="prose prose-invert max-w-none text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
        {result.answer}
      </div>

      {result.source_citations && result.source_citations.length > 0 && (
        <div className="mt-4">
          <button
            className="text-blue-400 text-sm hover:text-blue-300 transition-colors"
            onClick={() => setExpandedSources(!expandedSources)}
          >
            {expandedSources ? "Hide" : "Show"} sources (
            {result.source_citations.length})
          </button>
          {expandedSources && (
            <div className="mt-2 space-y-2">
              {result.source_citations.map((citation, i) => (
                <div
                  key={i}
                  className="p-3 bg-slate-800/50 rounded-lg text-slate-400 text-xs"
                >
                  {citation}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
