"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getGoalAuditTrail } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import {
  Scale,
  ArrowLeft,
  Clock,
  Cpu,
  Brain,
  CheckCircle,
  XCircle,
} from "lucide-react";
import Link from "next/link";

interface AuditEvent {
  timestamp: string;
  type: string;
  detail: Record<string, unknown>;
}

export default function AuditPage() {
  const { id: workspaceId, goalId } = useParams<{
    id: string;
    goalId: string;
  }>();
  const [goalText, setGoalText] = useState("");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState("");
  const [orgSlug, setOrgSlug] = useState<string | undefined>();

  useEffect(() => {
    const supabase = createClient();
    supabase.auth
      .getSession()
      .then(
        ({
          data: { session },
        }: {
          data: { session: import("@supabase/supabase-js").Session | null };
        }) => {
          if (session) {
            setToken(session.access_token);
            setOrgSlug(
              localStorage.getItem("legalrag_active_org") || undefined,
            );
          }
        },
      );
  }, []);

  useEffect(() => {
    if (!token || !workspaceId || !goalId) return;
    getGoalAuditTrail(token, workspaceId, goalId, orgSlug)
      .then((data) => {
        setGoalText(data.goal_text);
        setEvents(data.events);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, workspaceId, goalId, orgSlug]);

  const typeIcon = (type: string) => {
    switch (type) {
      case "intent_classification":
        return <Brain className="w-4 h-4 text-purple-400" />;
      case "workflow_status":
        return <Cpu className="w-4 h-4 text-blue-400" />;
      case "tool_call":
        return <Clock className="w-4 h-4 text-amber-400" />;
      default:
        return <CheckCircle className="w-4 h-4 text-slate-400" />;
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-navy-950 flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-accent-blue border-t-transparent animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-navy-950">
      <div className="max-w-4xl mx-auto px-4 py-8">
        <Link
          href={`/workspaces/${workspaceId}`}
          className="inline-flex items-center gap-2 text-slate-400 hover:text-white mb-4 text-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Workspace
        </Link>

        <div className="mb-6">
          <h1 className="text-xl font-bold text-white mb-2">Audit Trail</h1>
          <p className="text-slate-400 text-sm">{goalText}</p>
          <p className="text-slate-500 text-xs mt-1">{events.length} events</p>
        </div>

        <div className="space-y-2">
          {events.map((event, i) => (
            <div
              key={i}
              className="flex gap-3 p-3 bg-slate-900/50 border border-slate-800 rounded-lg"
            >
              <div className="mt-0.5">{typeIcon(event.type)}</div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-medium text-slate-300">
                    {event.type.replace(/_/g, " ")}
                  </span>
                  <span className="text-xs text-slate-500">
                    {new Date(event.timestamp).toLocaleString()}
                  </span>
                </div>
                <pre className="text-xs text-slate-400 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(event.detail, null, 2)}
                </pre>
              </div>
            </div>
          ))}
          {events.length === 0 && (
            <p className="text-slate-500 text-sm text-center py-8">
              No audit events found.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
