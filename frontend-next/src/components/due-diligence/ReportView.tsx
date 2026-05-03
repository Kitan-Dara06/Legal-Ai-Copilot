"use client";

import {
    CheckCircle2, AlertCircle, Terminal, CheckCircle, Scale
} from "lucide-react";
import type { LexToolCallLog } from "@/lib/types";

interface ReportViewProps {
    goal: string;
    findingsSummary: string;
    logs: LexToolCallLog[];
    onReset: () => void;
}

export function ReportView({ goal, findingsSummary, logs, onReset }: ReportViewProps) {
    const successCount = logs.filter(l => l.status === "SUCCESS").length;
    const failedCount = logs.filter(l => l.status === "FAILED").length;

    return (
        <div className="flex flex-col gap-6 w-full max-w-4xl mx-auto mt-6">
            {/* Report header card */}
            <div className="bg-gradient-to-br from-slate-900 to-slate-900/40 border border-slate-800 rounded-xl p-6">
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <div className="flex items-center gap-2 mb-2">
                            <Scale className="w-5 h-5 text-accent-blue" />
                            <p className="text-xs text-slate-500 uppercase tracking-widest font-semibold">Workflow Execution Report</p>
                        </div>
                        <p className="text-white font-semibold text-lg">{goal}</p>
                        {findingsSummary && (
                            <p className="text-sm text-slate-400 mt-2 leading-relaxed">
                                {findingsSummary}
                            </p>
                        )}
                    </div>
                    <button
                        onClick={onReset}
                        className="flex-shrink-0 text-sm font-medium text-slate-400 hover:text-white border border-slate-700 hover:border-slate-500 px-4 py-2 rounded-xl transition-all"
                    >
                        New Workflow
                    </button>
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-3 gap-3 mt-6">
                    <div className="bg-slate-950/60 border border-slate-800/50 rounded-xl px-4 py-3 text-center">
                        <p className="text-2xl font-bold text-white">{logs.length}</p>
                        <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-1">Total Actions</p>
                    </div>
                    <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl px-4 py-3 text-center">
                        <p className="text-2xl font-bold text-emerald-400">{successCount}</p>
                        <p className="text-[10px] text-emerald-500/70 uppercase tracking-wider mt-1">Successful</p>
                    </div>
                    <div className="bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3 text-center">
                        <p className="text-2xl font-bold text-red-400">{failedCount}</p>
                        <p className="text-[10px] text-red-500/70 uppercase tracking-wider mt-1">Failed</p>
                    </div>
                </div>
            </div>

            {/* Execution Logs */}
            {logs.length > 0 && (
                <div>
                    <p className="text-[10px] text-slate-500 uppercase tracking-widest mb-3 flex items-center gap-1.5 font-semibold">
                        <Terminal className="w-3.5 h-3.5" /> Execution Log ({logs.length})
                    </p>
                    <div className="flex flex-col gap-2">
                        {logs.map((log, idx) => (
                            <div key={log.id} className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 flex gap-4">
                                <div className="mt-0.5 flex-shrink-0">
                                    {log.status === "SUCCESS" ? (
                                        <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                                    ) : (
                                        <AlertCircle className="w-5 h-5 text-red-400" />
                                    )}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center justify-between gap-4">
                                        <p className="text-sm font-semibold text-slate-200">{log.tool_name}</p>
                                        <p className="text-xs text-slate-500 font-mono">
                                            {new Date(log.created_at).toLocaleTimeString()}
                                        </p>
                                    </div>
                                    <p className="text-sm text-slate-400 mt-1 leading-relaxed">
                                        {log.summary}
                                    </p>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
