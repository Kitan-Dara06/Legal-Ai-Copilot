"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { createClient } from "@/lib/supabase/client";
import { listEscalations } from "@/lib/api";
import {
  Mail, Calendar, Slack, Link2, AlertTriangle,
  CheckCircle2, XCircle, Settings2, Activity,
} from "lucide-react";
import type { EscalationItem } from "@/lib/types";

const INTEGRATIONS = [
  {
    key: "smtp",
    icon: Mail,
    label: "SMTP Email",
    desc: "Send deadline alerts and invite emails from your firm's address",
  },
  {
    key: "nylas",
    icon: Calendar,
    label: "Calendar Sync",
    desc: "Push extracted deadlines to Google Calendar or Outlook via Nylas",
  },
  {
    key: "slack",
    icon: Slack,
    label: "Slack Notifications",
    desc: "Notify your team channel when a workflow needs review or a deadline is near",
  },
  {
    key: "clio",
    icon: Link2,
    label: "Clio Integration",
    desc: "Sync matters and documents with your Clio practice management system",
  },
];

const SERVICES = ["Vector Search", "Graph DB", "Message Queue", "Database", "Cache"];

export default function AdminPage() {
  const router = useRouter();
  const [token, setToken]           = useState<string | null>(null);
  const [orgSlug, setOrgSlug]       = useState<string | undefined>();
  const [escalations, setEscalations] = useState<EscalationItem[]>([]);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data }: any) => {
      if (!data?.session) { router.push("/login"); return; }
      setToken(data.session.access_token);
      setOrgSlug(data.session.user?.user_metadata?.org_slug);
    });
  }, []);

  useEffect(() => {
    if (!token) return;
    listEscalations(token, orgSlug).then(setEscalations).catch(() => {});
  }, [token, orgSlug]);

  const unresolvedCount = escalations.filter((e) => e.status !== "COMPLETED").length;

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-[#F0EEE9] tracking-tight">Firm Settings</h1>
        <p className="text-sm text-[#7A7A8A] mt-1">
          Manage your firm's integrations, team access, and platform health.
        </p>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-2 gap-4 mb-8">
        <Card className="p-4">
          <div className="flex items-center gap-3">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${unresolvedCount > 0 ? "bg-[#F06B6B]/10" : "bg-[#3ECFA4]/10"}`}>
              {unresolvedCount > 0
                ? <AlertTriangle className="w-4 h-4 text-[#F06B6B]" />
                : <CheckCircle2 className="w-4 h-4 text-[#3ECFA4]" />}
            </div>
            <div>
              <p className="text-2xl font-bold text-[#F0EEE9]">{unresolvedCount}</p>
              <p className="text-xs text-[#7A7A8A]">Unresolved workflow failures</p>
            </div>
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[#7C6AF7]/10 flex items-center justify-center">
              <Activity className="w-4 h-4 text-[#7C6AF7]" />
            </div>
            <div>
              <p className="text-2xl font-bold text-[#F0EEE9]">{SERVICES.length}</p>
              <p className="text-xs text-[#7A7A8A]">Services connected</p>
            </div>
          </div>
        </Card>
      </div>

      {/* Integrations */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <Settings2 className="w-4 h-4 text-[#D4A853]" />
          <h2 className="text-sm font-semibold text-[#F0EEE9]">Integrations</h2>
        </div>
        <Card className="divide-y divide-[#2A2A32]">
          {INTEGRATIONS.map(({ key, icon: Icon, label, desc }) => (
            <div key={key} className="flex items-center justify-between px-4 py-3.5">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#1E1E28] border border-[#2A2A32] flex items-center justify-center shrink-0">
                  <Icon className="w-4 h-4 text-[#7A7A8A]" />
                </div>
                <div>
                  <p className="text-sm font-medium text-[#F0EEE9]">{label}</p>
                  <p className="text-[11px] text-[#4A4A5A] mt-0.5">{desc}</p>
                </div>
              </div>
              <Badge variant="warning">Not configured</Badge>
            </div>
          ))}
        </Card>
        <p className="text-[10px] text-[#4A4A5A] mt-2 px-1">
          Contact your account manager to enable integrations.
        </p>
      </div>

      {/* Service health */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <Activity className="w-4 h-4 text-[#D4A853]" />
          <h2 className="text-sm font-semibold text-[#F0EEE9]">Platform Health</h2>
        </div>
        <Card className="divide-y divide-[#2A2A32]">
          {SERVICES.map((service) => (
            <div key={service} className="flex items-center justify-between px-4 py-3">
              <span className="text-sm text-[#F0EEE9]">{service}</span>
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-[#3ECFA4] animate-pulse" />
                <span className="text-[11px] text-[#3ECFA4] font-medium">Operational</span>
              </div>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}
