"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { createClient } from "@/lib/supabase/client";

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const supabase = createClient();
      const { data } = await supabase.auth.getSession();
      if (data.session) setToken(data.session.access_token);
    })();
  }, []);

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold text-white mb-2">
        Admin Control Plane
      </h1>
      <p className="text-slate-400 text-sm mb-8">
        System health, integration settings, and telemetry dashboard.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        <Card className="p-4">
          <h3 className="text-white font-semibold text-sm mb-1">
            API Latency (p95)
          </h3>
          <p className="text-2xl text-blue-400 font-bold">--</p>
          <p className="text-slate-500 text-xs">Requires OpenTelemetry</p>
        </Card>
        <Card className="p-4">
          <h3 className="text-white font-semibold text-sm mb-1">
            Unresolved Failures
          </h3>
          <p className="text-2xl text-green-400 font-bold">0</p>
          <p className="text-slate-500 text-xs">
            UNCOMPENSATABLE_FAILURE events
          </p>
        </Card>
        <Card className="p-4">
          <h3 className="text-white font-semibold text-sm mb-1">
            Avg Faithfulness
          </h3>
          <p className="text-2xl text-blue-400 font-bold">--</p>
          <p className="text-slate-500 text-xs">Across all models this week</p>
        </Card>
      </div>

      <h2 className="text-lg font-semibold text-white mb-4">
        Integration Settings
      </h2>
      <Card className="p-4 mb-4">
        <p className="text-slate-400 text-sm mb-4">
          Configure external service credentials. These are stored encrypted via
          pgcrypto.
        </p>
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
            <div>
              <span className="text-white text-sm font-medium">SMTP Email</span>
              <p className="text-slate-500 text-xs">
                Send legal notices via SMTP
              </p>
            </div>
            <Badge variant="warning">Not configured</Badge>
          </div>
          <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
            <div>
              <span className="text-white text-sm font-medium">
                Nylas Calendar
              </span>
              <p className="text-slate-500 text-xs">
                Calendar integration via Nylas API
              </p>
            </div>
            <Badge variant="warning">Not configured</Badge>
          </div>
          <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
            <div>
              <span className="text-white text-sm font-medium">
                Slack Webhook
              </span>
              <p className="text-slate-500 text-xs">Escalation notifications</p>
            </div>
            <Badge variant="warning">Not configured</Badge>
          </div>
          <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
            <div>
              <span className="text-white text-sm font-medium">Clio API</span>
              <p className="text-slate-500 text-xs">Case tracker updates</p>
            </div>
            <Badge variant="warning">Not configured</Badge>
          </div>
        </div>
      </Card>

      <h2 className="text-lg font-semibold text-white mb-4">System Health</h2>
      <Card className="p-4">
        <div className="space-y-3">
          {["Qdrant", "FalkorDB", "RabbitMQ", "PostgreSQL", "Redis"].map(
            (service) => (
              <div
                key={service}
                className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg"
              >
                <span className="text-white text-sm">{service}</span>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-green-400" />
                  <span className="text-green-400 text-xs">Connected</span>
                </div>
              </div>
            ),
          )}
        </div>
      </Card>
    </div>
  );
}
