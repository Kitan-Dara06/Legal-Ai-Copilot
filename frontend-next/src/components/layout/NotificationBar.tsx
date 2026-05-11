"use client";

import { useEffect, useState } from "react";
import { NotificationBell } from "@/components/notifications/NotificationBell";

export function NotificationBar() {
    const [token, setToken] = useState<string | null>(null);
    const [orgSlug, setOrgSlug] = useState<string | null>(null);

    useEffect(() => {
        setToken(localStorage.getItem("sb-access-token"));
        setOrgSlug(localStorage.getItem("sb-org-slug"));
    }, []);

    return (
        <header className="border-b border-slate-800 bg-[#0A0F1E]/80 backdrop-blur-sm sticky top-0 z-50">
            <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <span className="text-lg font-bold text-white">Legal AI Copilot</span>
                </div>
                <div className="flex items-center gap-2">
                    {token && <NotificationBell token={token} orgSlug={orgSlug || undefined} />}
                </div>
            </div>
        </header>
    );
}
