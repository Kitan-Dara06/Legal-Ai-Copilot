"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { listDeadlines } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import {
  Scale,
  ArrowLeft,
  AlertTriangle,
  Clock,
  CheckCircle,
} from "lucide-react";
import Link from "next/link";

interface Deadline {
  id: string;
  obligation_description: string;
  obligation_type: string;
  raw_date_expression: string;
  resolved_deadline: string | null;
  resolution_status: string;
  conflict_flag: boolean;
  status: string;
  urgency_score: number;
}

export default function DeadlinesPage() {
  const { id: workspaceId } = useParams<{ id: string }>();
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
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
    if (!token || !workspaceId) return;
    listDeadlines(token, workspaceId, orgSlug)
      .then(setDeadlines)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token, workspaceId, orgSlug]);

  const statusBadge = (status: string) => {
    const styles: Record<string, string> = {
      ACTIVE: "bg-blue-500/20 text-blue-400",
      OVERDUE: "bg-red-500/20 text-red-400",
      COMPLETED: "bg-emerald-500/20 text-emerald-400",
      SUPERSEDED: "bg-slate-500/20 text-slate-400",
    };
    return styles[status] || "bg-slate-500/20 text-slate-400";
  };

  const urgencyColor = (score: number) => {
    if (score >= 0.9) return "text-red-400";
    if (score >= 0.7) return "text-amber-400";
    if (score >= 0.5) return "text-yellow-400";
    return "text-slate-400";
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

        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-bold text-white">Deadlines</h1>
          <span className="text-xs text-slate-500">
            {deadlines.length} entries
          </span>
        </div>

        <div className="space-y-3">
          {deadlines.map((dl) => (
            <div
              key={dl.id}
              className="bg-slate-900/50 border border-slate-800 rounded-lg p-4"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex-1 min-w-0">
                  <p className="text-white text-sm font-medium">
                    {dl.obligation_description}
                  </p>
                  <p className="text-slate-500 text-xs mt-0.5">
                    {dl.obligation_type} | Source: &quot;
                    {dl.raw_date_expression}&quot;
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-4">
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded ${statusBadge(dl.status)}`}
                  >
                    {dl.status}
                  </span>
                  {dl.conflict_flag && (
                    <AlertTriangle className="w-4 h-4 text-red-400" />
                  )}
                </div>
              </div>

              <div className="flex items-center gap-4 text-xs text-slate-500">
                {dl.resolved_deadline && (
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {new Date(dl.resolved_deadline).toLocaleDateString()}
                  </span>
                )}
                <span
                  className={`flex items-center gap-1 ${urgencyColor(dl.urgency_score)}`}
                >
                  Urgency: {(dl.urgency_score * 100).toFixed(0)}%
                </span>
                <span>Status: {dl.resolution_status}</span>
              </div>
            </div>
          ))}
          {deadlines.length === 0 && (
            <p className="text-slate-500 text-sm text-center py-8">
              No deadlines found.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
