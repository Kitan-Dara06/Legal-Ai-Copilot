"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

export default function ApprovalsPage() {
    const router = useRouter();
    const [token, setToken] = useState<string | null>(null);
    const [orgSlug, setOrgSlug] = useState<string | null>(null);

    useEffect(() => {
        setToken(localStorage.getItem("sb-access-token"));
        setOrgSlug(localStorage.getItem("sb-org-slug"));
    }, []);

    return (
        <div className="max-w-4xl mx-auto px-4 py-8">
            <h1 className="text-2xl font-bold text-white mb-2">Approval Inbox</h1>
            <p className="text-slate-400 text-sm mb-8">
                Review and approve or reject drafted actions before execution.
            </p>

            <Card className="p-8 text-center">
                <p className="text-slate-400">No pending approvals.</p>
                <p className="text-slate-500 text-sm mt-2">
                    Drafted actions requiring your sign-off will appear here.
                </p>
            </Card>
        </div>
    );
}
