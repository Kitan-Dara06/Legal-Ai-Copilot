"use client";

export const dynamic = "force-dynamic";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent } from "@/components/ui/Card";
import { Scale } from "lucide-react";
import { setupOrg } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

export default function SetupPage() {
    const [orgName, setOrgName] = useState("");
    const [orgId, setOrgId] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");

    const handleSetup = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsLoading(true);
        setError("");

        try {
            const supabase = createClient();
            const {
                data: { session },
            } = await supabase.auth.getSession();

            if (!session) {
                window.location.href = "/login";
                return;
            }

            await setupOrg(session.access_token, {
                org_id: orgId.toLowerCase().trim(),
                org_name: orgName.trim(),
            });

            // Redirect to chat on success
            window.location.href = "/chat";
        } catch (err: any) {
            setError(err.message || "Failed to setup workspace");
            setIsLoading(false);
        }
    };

    return (
        <div className="min-h-screen bg-navy-950 flex flex-col items-center justify-center p-4">
            {/* Branding */}
            <div className="mb-8 text-center flex flex-col items-center">
                <div className="bg-accent-blue/10 p-3 rounded-2xl mb-4 border border-accent-blue/20 shadow-[0_0_30px_rgba(59,130,246,0.15)]">
                    <Scale
                        className="w-10 h-10 text-accent-blue"
                        strokeWidth={1.5}
                    />
                </div>
                <h1 className="text-3xl font-bold tracking-tight text-white mb-2">
                    Workspace Setup
                </h1>
                <p className="text-slate-400 max-w-sm">
                    You're signed in! Let's create your organization's secure
                    workspace.
                </p>
            </div>

            <Card className="w-full max-w-md border-slate-800/50 bg-slate-900/50">
                <CardContent className="pt-6">
                    <form onSubmit={handleSetup} className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium text-slate-300">
                                Law Firm / Company Name
                            </label>
                            <Input
                                placeholder="e.g. Acme Legal LLP"
                                value={orgName}
                                onChange={(e) => setOrgName(e.target.value)}
                                required
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium text-slate-300">
                                Workspace Slug (ID)
                            </label>
                            <Input
                                placeholder="e.g. acme-legal"
                                value={orgId}
                                onChange={(e) => setOrgId(e.target.value)}
                                pattern="^[a-z0-9][a-z0-9-]{2,50}$"
                                title="3-50 characters, letters, numbers, and hyphens only, must start with letter/number"
                                required
                            />
                            <p className="text-xs text-slate-500 mt-1">
                                This will be your unique identifier on the
                                platform.
                            </p>
                        </div>

                        {error && (
                            <div className="text-sm text-red-500 bg-red-500/10 p-3 rounded">
                                {error}
                            </div>
                        )}

                        <Button
                            type="submit"
                            className="w-full mt-6"
                            isLoading={isLoading}
                        >
                            Create Workspace
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}
