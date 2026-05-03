"use client";

import { useState } from "react";
import { CheckCircle, Trash2, ChevronDown, ChevronUp, AlertTriangle } from "lucide-react";
import type { LexAction } from "@/lib/types";

interface PlanReviewProps {
    workflowId: string;
    actions: LexAction[];
    onConfirm: () => void;
    isExecuting: boolean;
}

const TASK_TYPE_COLOUR: Record<string, string> = {
    SEND_NOTICE:       "bg-blue-500/10 text-blue-400 border-blue-500/20",
    DRAFT_RESPONSE:    "bg-violet-500/10 text-violet-400 border-violet-500/20",
    FILE_DOCUMENT:     "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    UPDATE_CASE_TRACKER:"bg-teal-500/10 text-teal-400 border-teal-500/20",
    FLAG_FOR_REVIEW:   "bg-pink-500/10 text-pink-400 border-pink-500/20",
    ESCALATE_TO_COUNSEL:"bg-amber-500/10 text-amber-400 border-amber-500/20",
};

export function PlanReview({ workflowId, actions: initialActions, onConfirm, isExecuting }: PlanReviewProps) {
    const [actions, setActions] = useState<LexAction[]>(initialActions);

    const typeColour = (type: string) =>
        TASK_TYPE_COLOUR[type] ?? "bg-slate-500/10 text-slate-400 border-slate-500/20";

    return (
        <div className="flex flex-col gap-6 w-full max-w-4xl mx-auto mt-6">
            {/* Instruction strip */}
            <div className="flex items-start gap-3 bg-amber-500/5 border border-amber-500/20 rounded-xl px-4 py-3">
                <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
                <p className="text-xs text-amber-300/80 leading-relaxed">
                    Review the task plan below. The agent will execute exactly these tasks — in order — once you approve. 
                    Tasks are ordered by urgency score.
                </p>
            </div>

            {/* Task list */}
            <div className="flex flex-col gap-2">
                {actions.length === 0 ? (
                    <div className="text-center p-8 bg-slate-900/40 rounded-xl border border-slate-800">
                        <p className="text-slate-400 text-sm">No actions detected for this workflow.</p>
                    </div>
                ) : (
                    actions.map((action, idx) => (
                        <div
                            key={action.id}
                            className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden transition-all"
                        >
                            <div className="flex items-center gap-3 px-4 py-3">
                                <span className="text-slate-600 text-sm font-mono w-5 text-center flex-shrink-0">
                                    {idx + 1}
                                </span>
                                <span className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border flex-shrink-0 ${typeColour(action.action_type)}`}>
                                    {action.action_type}
                                </span>
                                <p className="text-sm text-slate-300 flex-1 truncate">{action.description}</p>
                                <div className="text-xs text-slate-500 font-mono flex items-center gap-2">
                                    <span>Urgency:</span>
                                    <span className={`${action.urgency >= 0.8 ? "text-amber-400" : action.urgency >= 0.5 ? "text-blue-400" : "text-slate-400"}`}>
                                        {action.urgency.toFixed(2)}
                                    </span>
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>

            {/* Actions */}
            <div className="flex items-center justify-end gap-3 pt-2">
                <button
                    onClick={onConfirm}
                    disabled={isExecuting}
                    className="flex items-center gap-2 px-6 py-2.5 bg-accent-blue hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all shadow-lg shadow-blue-500/20"
                >
                    {isExecuting ? (
                        <>
                            <span className="w-3.5 h-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                            Executing…
                        </>
                    ) : (
                        <>
                            <CheckCircle className="w-4 h-4" />
                            Approve &amp; Execute
                        </>
                    )}
                </button>
            </div>
        </div>
    );
}
