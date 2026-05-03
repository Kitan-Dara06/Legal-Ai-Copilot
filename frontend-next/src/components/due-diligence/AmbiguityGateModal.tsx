import React, { useState } from "react";
import { AlertTriangle, Brain, Sparkles, Check, ChevronRight } from "lucide-react";

interface AmbiguityGateModalProps {
    guessedIntent: string;
    confidence: number;
    onConfirm: (intent: string) => Promise<void>;
}

export function AmbiguityGateModal({ guessedIntent, confidence, onConfirm }: AmbiguityGateModalProps) {
    const [selectedIntent, setSelectedIntent] = useState<string>(guessedIntent || "ANALYZE");
    const [submitting, setSubmitting] = useState(false);

    const intentDescriptions: Record<string, string> = {
        ANALYZE: "A question requiring grounded retrieval (e.g. 'What does X mean?').",
        REASON: "Requires cross-document analysis or risk assessment (e.g. 'Compare X and Y').",
        ACT: "Requires drafting, approval, and execution of a legal action (e.g. 'Send notice').",
    };

    const handleConfirm = async () => {
        setSubmitting(true);
        try {
            await onConfirm(selectedIntent);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
            <div className="bg-slate-900 border border-slate-800 shadow-2xl rounded-2xl w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95 duration-200">
                {/* Header */}
                <div className="p-6 border-b border-slate-800 bg-slate-900/50">
                    <div className="flex items-center gap-3 text-amber-400 mb-2">
                        <AlertTriangle className="w-6 h-6" />
                        <h2 className="text-lg font-bold text-slate-100">Clarification Needed</h2>
                    </div>
                    <p className="text-sm text-slate-400">
                        The AI analyzed your goal but needs confirmation on how to proceed. 
                        Confidence score: <span className="text-slate-300 font-mono">{(confidence * 100).toFixed(0)}%</span>
                    </p>
                </div>

                {/* Body */}
                <div className="p-6 space-y-6">
                    <div>
                        <p className="text-sm text-slate-300 mb-3">
                            Based on your request, I think you want to:
                        </p>
                        <div className="bg-accent-blue/10 border border-accent-blue/20 rounded-xl p-4 flex gap-4">
                            <Brain className="w-8 h-8 text-accent-blue flex-shrink-0" />
                            <div>
                                <h3 className="text-accent-blue font-semibold text-lg">{guessedIntent}</h3>
                                <p className="text-sm text-slate-400 mt-1">{intentDescriptions[guessedIntent]}</p>
                            </div>
                        </div>
                    </div>

                    <div>
                        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
                            Select the correct path:
                        </p>
                        <div className="space-y-2">
                            {["ANALYZE", "REASON", "ACT"].map((intent) => {
                                const isSelected = selectedIntent === intent;
                                return (
                                    <button
                                        key={intent}
                                        onClick={() => setSelectedIntent(intent)}
                                        className={`w-full text-left px-4 py-3 rounded-xl border transition-all flex items-center justify-between ${
                                            isSelected 
                                                ? "border-accent-blue bg-accent-blue/5 text-slate-200" 
                                                : "border-slate-800 hover:border-slate-700 text-slate-400 hover:text-slate-300"
                                        }`}
                                    >
                                        <div>
                                            <div className="font-medium text-sm flex items-center gap-2">
                                                {intent}
                                                {isSelected && <span className="text-[10px] bg-accent-blue text-white px-2 py-0.5 rounded-full">Selected</span>}
                                            </div>
                                            <div className="text-xs mt-0.5 opacity-80">{intentDescriptions[intent]}</div>
                                        </div>
                                        {isSelected && <Check className="w-5 h-5 text-accent-blue" />}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                </div>

                {/* Footer */}
                <div className="p-5 border-t border-slate-800 bg-slate-900/80 flex justify-end gap-3">
                    <button
                        onClick={handleConfirm}
                        disabled={submitting}
                        className="flex items-center gap-2 px-6 py-2.5 bg-accent-blue hover:bg-blue-600 text-white font-medium rounded-xl transition-all shadow-lg shadow-blue-500/20 disabled:opacity-50"
                    >
                        {submitting ? "Confirming..." : "Confirm & Proceed"}
                        {!submitting && <ChevronRight className="w-4 h-4" />}
                    </button>
                </div>
            </div>
        </div>
    );
}
