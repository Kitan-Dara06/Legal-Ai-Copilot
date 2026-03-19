"use client";

import { useState, useEffect } from "react";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { createClient } from "@/lib/supabase/client";

export function SignupForm() {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [orgName, setOrgName] = useState("");
    const [orgId, setOrgId] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    const [success, setSuccess] = useState(false);
    const supabase = createClient();

    useEffect(() => {
        // Clear any existing session to prevent organization crosstalk
        // when signing up a new account.
        supabase.auth.signOut();
    }, [supabase.auth]);

    const handleSignup = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsLoading(true);
        setError("");

        // 1. Sign up with Supabase
        const { data: authData, error: authError } = await supabase.auth.signUp(
            {
                email,
                password,
            },
        );

        if (authError) {
            setError(authError.message);
            setIsLoading(false);
            return;
        }

        if (!authData.session) {
            setSuccess(true);
            setIsLoading(false);
            return;
        }

        // 2. Setup Organization via API
        try {
            const res = await fetch(
                `${process.env.NEXT_PUBLIC_API_URL}/auth/setup-org`,
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Authorization: `Bearer ${authData.session.access_token}`,
                    },
                    body: JSON.stringify({
                        org_id: orgId.toLowerCase().trim(),
                        org_name: orgName.trim(),
                    }),
                },
            );

            if (!res.ok) {
                let msg = "Failed to setup workspace.";
                try {
                    const body = await res.json();
                    msg = body.detail || msg;
                } catch {}
                setError(msg);
            } else {
                window.location.href = "/chat";
            }
        } catch (err: any) {
            setError(err.message || "Network error");
        } finally {
            setIsLoading(false);
        }
    };

    if (success) {
        return (
            <div className="text-center space-y-4">
                <h3 className="text-xl font-semibold text-white">
                    Check your email
                </h3>
                <p className="text-slate-400">
                    We sent a confirmation link to {email}. Please click it to
                    activate your account.
                </p>
            </div>
        );
    }

    return (
        <form onSubmit={handleSignup} className="space-y-4">
            <div className="space-y-2">
                <label className="text-sm font-medium text-slate-300">
                    Email
                </label>
                <Input
                    type="email"
                    placeholder="your.email@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                />
            </div>
            <div className="space-y-2">
                <label className="text-sm font-medium text-slate-300">
                    Password
                </label>
                <div className="relative">
                    <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="••••••••"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                        minLength={8}
                        className="pr-12"
                    />
                    <button
                        type="button"
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-200 focus:outline-none"
                        onClick={() => setShowPassword(!showPassword)}
                    >
                        {showPassword ? "Hide" : "Show"}
                    </button>
                </div>
            </div>

            <div className="pt-4 border-t border-slate-800">
                <h4 className="text-sm font-medium text-white mb-4">
                    Create your workspace
                </h4>
                <div className="space-y-4">
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
                    </div>
                </div>
            </div>

            {error && <div className="text-sm text-red-500">{error}</div>}
            <Button type="submit" className="w-full mt-6" isLoading={isLoading}>
                Create Account & Workspace
            </Button>
        </form>
    );
}
