"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";

interface Action {
  id: string;
  action_type: string;
  description: string;
  status: string;
  urgency: number;
}

interface ActionQueueProps {
  actions: Action[];
  workflowId?: string;
  draft?: string | null;
  token?: string;
  orgSlug?: string;
}

export function ActionQueue({
  actions,
  workflowId,
  draft,
  token,
  orgSlug,
}: ActionQueueProps) {
  const [loading, setLoading] = useState(false);
  const [approved, setApproved] = useState(false);
  const [rejected, setRejected] = useState(false);

  const handleApprove = async () => {
    if (!token || !workflowId) return;
    setLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "";
      const res = await fetch(`${baseUrl}/api/agent/approve/${workflowId}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          ...(orgSlug ? { "x-org-slug": orgSlug } : {}),
        },
      });
      if (res.ok) {
        setApproved(true);
      }
    } catch (err) {
      console.error("Approve failed:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    if (!token || !workflowId) return;
    setLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "";
      await fetch(`${baseUrl}/api/agent/reject/${workflowId}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          ...(orgSlug ? { "x-org-slug": orgSlug } : {}),
        },
        body: JSON.stringify({ reason: "" }),
      });
      setRejected(true);
    } catch (err) {
      console.error("Reject failed:", err);
    } finally {
      setLoading(false);
    }
  };

  if (approved) {
    return (
      <div className="border border-green-700 rounded-lg p-4 my-3 mx-4 bg-green-900/20">
        <p className="text-green-400 text-sm font-medium">
          ✅ Approved — executing actions
        </p>
      </div>
    );
  }

  if (rejected) {
    return (
      <div className="border border-red-700 rounded-lg p-4 my-3 mx-4 bg-red-900/20">
        <p className="text-red-400 text-sm font-medium">❌ Plan rejected</p>
      </div>
    );
  }

  return (
    <div className="border border-slate-700 rounded-lg p-4 my-3 mx-4 bg-slate-900/50">
      <h3 className="text-sm font-semibold text-slate-300 mb-3">
        📋 Action Plan
      </h3>
      <div className="space-y-2">
        {actions.map((action) => (
          <div
            key={action.id}
            className="flex items-start gap-3 p-2 rounded-md"
          >
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
            </div>
          </div>
        ))}
      </div>

      {draft && (
        <div className="mt-4 pt-4 border-t border-slate-700">
          <h4 className="text-sm font-semibold text-slate-300 mb-2">
            📄 Draft Preview
          </h4>
          <div className="prose prose-invert prose-sm max-w-none bg-slate-950 rounded p-3 text-slate-300 whitespace-pre-wrap">
            {draft}
          </div>
        </div>
      )}

      <div className="flex gap-3 mt-4 pt-4 border-t border-slate-700">
        <Button variant="outline" onClick={handleReject} disabled={loading}>
          Reject
        </Button>
        <Button onClick={handleApprove} disabled={loading}>
          {loading ? "Processing..." : "Approve & Execute"}
        </Button>
      </div>
    </div>
  );
}
