"use client";

import { useEffect, useState, useCallback } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { listEscalations, resolveEscalation } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { CheckCircle, AlertCircle, Loader2 } from "lucide-react";

type Escalation = {
  id: string;
  status: string;
  intent: string;
  error_context?: string;
  created_at: string;
};

function getStatusVariant(
  status: string,
): "default" | "success" | "warning" | "error" | "info" {
  const s = status.toLowerCase();
  if (s === "resolved" || s === "closed") return "success";
  if (s === "open" || s === "pending" || s === "active") return "warning";
  if (s === "failed" || s === "error") return "error";
  if (s === "escalated") return "info";
  return "default";
}

export default function EscalationsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [orgSlug, setOrgSlug] = useState<string | null>(null);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        setToken(session.access_token);
        setOrgSlug(localStorage.getItem("legalrag_active_org"));
      }
    });
  }, []);

  const fetchEscalations = useCallback(async () => {
    if (!token) return;
    try {
      const data = await listEscalations(token, orgSlug || undefined);
      setEscalations(data);
    } catch (err) {
      console.error("Failed to load escalations:", err);
    } finally {
      setLoading(false);
    }
  }, [token, orgSlug]);

  useEffect(() => {
    fetchEscalations();
  }, [fetchEscalations]);

  // Auto-dismiss message after 5 seconds
  useEffect(() => {
    if (message) {
      const timer = setTimeout(() => setMessage(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [message]);

  const handleResolve = async (escalation: Escalation) => {
    if (!token) return;
    setActionLoading(escalation.id);
    setMessage(null);
    try {
      const result = await resolveEscalation(
        token,
        escalation.id,
        orgSlug || undefined,
      );
      setMessage({ type: "success", text: result.message });
      // Remove from local list on success
      setEscalations((prev) => prev.filter((e) => e.id !== escalation.id));
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : "Failed to resolve escalation.";
      setMessage({ type: "error", text: errorMsg });
      console.error("Resolve failed:", err);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold text-white mb-2">Escalations</h1>
      <p className="text-slate-400 text-sm mb-8">
        Manage system escalations requiring admin attention.
      </p>

      {/* Success / Error toast */}
      {message && (
        <div
          className={`mb-6 flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${
            message.type === "success"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
              : "border-red-500/30 bg-red-500/10 text-red-400"
          }`}
        >
          {message.type === "success" ? (
            <CheckCircle className="h-4 w-4 shrink-0" />
          ) : (
            <AlertCircle className="h-4 w-4 shrink-0" />
          )}
          <span>{message.text}</span>
        </div>
      )}

      {loading ? (
        <Card className="p-8 text-center">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400 mx-auto mb-2" />
          <p className="text-slate-400">Loading escalations...</p>
        </Card>
      ) : escalations.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-slate-400">No active escalations.</p>
          <p className="text-slate-500 text-sm mt-2">
            INVARIANT_VIOLATION, UNCOMPENSATABLE_FAILURE, and expired TTL gates
            will appear here.
          </p>
        </Card>
      ) : (
        <div className="space-y-4">
          {escalations.map((escalation) => (
            <Card key={escalation.id} className="p-4">
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  {/* Badge row */}
                  <div className="flex items-center gap-2 mb-2">
                    <Badge variant={getStatusVariant(escalation.status)}>
                      {escalation.status}
                    </Badge>
                  </div>

                  {/* Intent */}
                  <p className="text-white text-sm font-medium">
                    {escalation.intent}
                  </p>

                  {/* Error context */}
                  {escalation.error_context && (
                    <p className="text-slate-500 text-xs mt-1.5 font-mono line-clamp-2">
                      {escalation.error_context}
                    </p>
                  )}

                  {/* Metadata line */}
                  <p className="text-slate-500 text-xs mt-2">
                    ID: {escalation.id.slice(0, 8)}... &nbsp;·&nbsp;Created:{" "}
                    {new Date(escalation.created_at).toLocaleString()}
                  </p>
                </div>

                {/* Resolve button */}
                <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                  <Button
                    size="sm"
                    onClick={() => handleResolve(escalation)}
                    disabled={actionLoading === escalation.id}
                  >
                    {actionLoading === escalation.id ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                        Resolving...
                      </>
                    ) : (
                      "Resolve"
                    )}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
