"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

export default function EscalationsPage() {
    const [token, setToken] = useState<string | null>(null);
    const [orgSlug, setOrgSlug] = useState<string | null>(null);

    useEffect(() => {
        setToken(localStorage.getItem("sb-access-token"));
        setOrgSlug(localStorage.getItem("sb-org-slug"));
    }, []);

    return (
        <div className="max-w-4xl mx-auto px-4 py-8">
            <h1 className="text-2xl font-bold text-white mb-2">Escalations</h1>
            <p className="text-slate-400 text-sm mb-8">
                Manage system escalations requiring admin attention.
            </p>

            <Card className="p-8 text-center">
                <p className="text-slate-400">No active escalations.</p>
                <p className="text-slate-500 text-sm mt-2">
                    INVARIANT_VIOLATION, UNCOMPENSATABLE_FAILURE, and expired TTL gates will appear here.
                </p>
            </Card>
        </div>
    );
}
