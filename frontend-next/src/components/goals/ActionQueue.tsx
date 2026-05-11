"use client";

import { useState } from "react";

interface Action {
  id: string;
  action_type: string;
  description: string;
  status: string;
  urgency: number;
}

interface ActionQueueProps {
  actions: Action[];
  draft?: string | null;
}

export function ActionQueue({ actions, draft }: ActionQueueProps) {
  const [checked, setChecked] = useState<Set<string>>(() => new Set(actions.map((a) => a.id)));

  const toggleAction = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const dependencyMessage = (action: Action): string | null => {
    // Simple dependency check: actions with "DRAFT" type depend on earlier "RESEARCH" actions
    if (action.action_type === "DRAFT" && !actions.some((a) => a.action_type === "RESEARCH" && checked.has(a.id))) {
      return "Requires a completed research step first";
    }
    if (action.action_type === "EXECUTE" && !actions.some((a) => a.action_type === "DRAFT" && checked.has(a.id))) {
      return "Requires a completed draft step first";
    }
    return null;
  };

  return (
    <div className="border border-slate-700 rounded-lg p-4 my-3 mx-4 bg-slate-900/50">
      <h3 className="text-sm font-semibold text-slate-300 mb-3">📋 Action Plan</h3>
      <div className="space-y-2">
        {actions.map((action) => {
          const depMsg = dependencyMessage(action);
          const isDisabled = depMsg !== null && !checked.has(action.id);

          return (
            <div
              key={action.id}
              className={`flex items-start gap-3 p-2 rounded-md transition-colors ${
                isDisabled ? "opacity-50" : ""
              }`}
            >
              <input
                type="checkbox"
                checked={checked.has(action.id)}
                onChange={() => toggleAction(action.id)}
                disabled={depMsg !== null}
                className="mt-0.5"
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-200">{action.description}</p>
                <div className="flex gap-2 mt-1">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">
                    {action.action_type}
                  </span>
                  {action.urgency >= 7 && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-red-900/50 text-red-400">
                      High urgency
                    </span>
                  )}
                </div>
                {depMsg && !checked.has(action.id) && (
                  <p className="text-xs text-amber-400 mt-1">{depMsg}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {draft && (
        <div className="mt-4 pt-4 border-t border-slate-700">
          <h4 className="text-sm font-semibold text-slate-300 mb-2">📄 Draft Preview</h4>
          <div className="prose prose-invert prose-sm max-w-none bg-slate-950 rounded p-3 text-slate-300 whitespace-pre-wrap">
            {draft}
          </div>
        </div>
      )}
    </div>
  );
}
