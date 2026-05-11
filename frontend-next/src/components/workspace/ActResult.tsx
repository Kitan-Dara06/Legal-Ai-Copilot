"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { LexAction, DependencyInfo } from "@/lib/types";

interface ActResultProps {
    actions: LexAction[];
    dependencies?: DependencyInfo[];
    workflowId: string;
    onApprove?: (workflowId: string) => void;
    onReject?: (workflowId: string) => void;
}

const URGENCY_COLORS: Record<number, string> = {
    0: "slate",
    0.3: "yellow",
    0.5: "orange",
    0.7: "red",
    0.9: "red",
};

function getUrgencyColor(score: number): string {
    if (score >= 0.9) return "red";
    if (score >= 0.7) return "orange";
    if (score >= 0.5) return "yellow";
    if (score >= 0.3) return "blue";
    return "slate";
}

export function ActResult({ actions, dependencies, workflowId, onApprove, onReject }: ActResultProps) {
    const [selectedActions, setSelectedActions] = useState<Set<string>>(
        new Set(actions.map(a => a.id))
    );
    const [validationError, setValidationError] = useState<string | null>(null);

    const sortedActions = [...actions].sort((a, b) => b.urgency - a.urgency);

    const toggleAction = (actionId: string) => {
        setValidationError(null);
        const newSelected = new Set(selectedActions);

        if (newSelected.has(actionId)) {
            // Check if any remaining action depends on this one
            if (dependencies) {
                for (const dep of dependencies) {
                    if (dep.depends_on.includes(actionId) && newSelected.has(dep.action_id)) {
                        const actionName = actions.find(a => a.id === dep.action_id)?.description || dep.action_id;
                        setValidationError(
                            `Cannot remove this action because "${actionName}" depends on it.`
                        );
                        return;
                    }
                }
            }
            newSelected.delete(actionId);
        } else {
            newSelected.add(actionId);
        }
        setSelectedActions(newSelected);
    };

    return (
        <Card className="mb-6 p-4">
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-white font-semibold text-lg">Action Plan</h3>
                <Badge color="blue">{actions.length} actions</Badge>
            </div>

            {/* Dependency Validation Error */}
            {validationError && (
                <div className="mb-4 p-3 bg-red-900/30 border border-red-500/30 rounded-lg text-red-400 text-sm">
                    {validationError}
                </div>
            )}

            {/* Action Queue */}
            <div className="space-y-2">
                {sortedActions.map((action) => (
                    <div
                        key={action.id}
                        className={`flex items-center justify-between p-3 rounded-lg transition-colors ${
                            selectedActions.has(action.id)
                                ? "bg-slate-800/50 border border-slate-700"
                                : "bg-slate-800/20 border border-slate-700/50 opacity-50"
                        }`}
                    >
                        <div className="flex items-center gap-3">
                            <input
                                type="checkbox"
                                checked={selectedActions.has(action.id)}
                                onChange={() => toggleAction(action.id)}
                                className="rounded border-slate-600"
                            />
                            <div>
                                <p className="text-white text-sm font-medium">{action.description}</p>
                                <p className="text-slate-500 text-xs mt-0.5">{action.action_type}</p>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            {/* Urgency bar */}
                            <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                <div
                                    className={`h-full rounded-full transition-all ${
                                        action.urgency >= 0.7 ? "bg-red-500" :
                                        action.urgency >= 0.5 ? "bg-yellow-500" :
                                        "bg-blue-500"
                                    }`}
                                    style={{ width: `${Math.min(action.urgency * 100, 100)}%` }}
                                />
                            </div>
                            <span className="text-slate-400 text-xs w-8 text-right">
                                {(action.urgency * 100).toFixed(0)}%
                            </span>
                            <Badge color={action.status === "DETECTED" ? "yellow" : "blue"} size="sm">
                                {action.status}
                            </Badge>
                        </div>
                    </div>
                ))}
            </div>

            {/* Approve / Reject */}
            <div className="flex items-center justify-end gap-3 mt-6 pt-4 border-t border-slate-700">
                <Button
                    variant="secondary"
                    onClick={() => onReject?.(workflowId)}
                >
                    Reject Plan
                </Button>
                <Button
                    onClick={() => onApprove?.(workflowId)}
                >
                    Approve & Execute
                </Button>
            </div>
        </Card>
    );
}
