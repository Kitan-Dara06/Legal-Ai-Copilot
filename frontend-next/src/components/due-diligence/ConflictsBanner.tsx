"use client";

import { AlertTriangle, X } from "lucide-react";
import type { DefinitionalConflict } from "@/lib/types";

interface ConflictsBannerProps {
    conflicts: DefinitionalConflict[];
    onDismiss: () => void;
}

export function ConflictsBanner({ conflicts, onDismiss }: ConflictsBannerProps) {
    if (conflicts.length === 0) return null;

    return (
        <div className="flex items-start gap-3 bg-amber-500/5 border border-amber-500/30 rounded-xl px-4 py-3">
            <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-amber-300">
                    {conflicts.length} Definitional Conflict{conflicts.length !== 1 ? "s" : ""} Detected
                </p>
                <p className="text-[10px] text-amber-300/60 mt-0.5">
                    Terms defined differently across documents:{" "}
                    {conflicts
                        .slice(0, 4)
                        .map((c) => `"${c.term}"`)
                        .join(", ")}
                    {conflicts.length > 4 ? ` +${conflicts.length - 4} more` : ""}
                </p>
            </div>
            <button
                onClick={onDismiss}
                className="text-amber-400/40 hover:text-amber-400 transition-colors flex-shrink-0"
            >
                <X className="w-3.5 h-3.5" />
            </button>
        </div>
    );
}
