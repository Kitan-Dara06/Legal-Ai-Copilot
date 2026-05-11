"use client";

import { type GoalIntent } from "@/lib/types";

interface AmbiguityGateProps {
  goalText: string;
  goalId: string;
  onConfirm: (intent: GoalIntent) => void;
}

export function AmbiguityGate({ goalText, goalId, onConfirm }: AmbiguityGateProps) {
  // Extract a key phrase from the goal text for the button labels
  const keyPhrase = goalText.length > 50
    ? goalText.substring(0, 50) + "..."
    : goalText;

  return (
    <div className="border border-amber-600/30 bg-amber-950/20 rounded-lg p-4 my-3 mx-4">
      <p className="text-amber-300 text-sm font-medium mb-3">
        ⚖️ I want to make sure I get this right. Would you like me to:
      </p>
      <div className="space-y-2">
        <button
          onClick={() => onConfirm("ANALYZE")}
          className="w-full text-left px-4 py-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors text-sm"
        >
          <span className="text-slate-300">📖 Read and summarize</span>
          <span className="text-slate-400 ml-1">"{keyPhrase}"</span>
        </button>
        <button
          onClick={() => onConfirm("REASON")}
          className="w-full text-left px-4 py-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors text-sm"
        >
          <span className="text-slate-300">🔗 Cross-check</span>
          <span className="text-slate-400 ml-1">"{keyPhrase}" across all documents</span>
        </button>
        <button
          onClick={() => onConfirm("ACT")}
          className="w-full text-left px-4 py-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors text-sm"
        >
          <span className="text-slate-300">✍️ Draft a completely new</span>
          <span className="text-slate-400 ml-1">"{keyPhrase}"</span>
        </button>
      </div>
    </div>
  );
}
