"use client";

import { useState } from "react";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { createClient } from "@/lib/supabase/client";
import { getMe, AppError } from "@/lib/api";
import { useSearchParams } from "next/navigation";

export function LoginForm() {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    const supabase = createClient();
    const searchParams = useSearchParams();
    const redirectUrl = searchParams?.get("redirect");

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsLoading(true);
        setError("");

        const { data, error: signInError } = await supabase.auth.signInWithPassword({
            email,
            password,
        });

        if (signInError) {
            setError(signInError.message);
            setIsLoading(false);
            return;
        }

        // Check whether this user has a local DB record (org set up).
        // Route to /setup if not, /chat or custom redirect if all good.
        try {
            await getMe(data.session!.access_token);
            window.location.href = redirectUrl || "/chat";
        } catch (err) {
            if (err instanceof AppError && (err.code === "setup_required" || err.status === 403)) {
                // Ignore redirect for setup because they strictly must do setup
                window.location.href = "/setup";
            } else {
                // Unexpected error 
                window.location.href = redirectUrl || "/chat";
            }
        }
    };

    return (
        <form onSubmit={handleLogin} className="space-y-4">
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
            {error && <div className="text-sm text-red-500">{error}</div>}
            <Button type="submit" className="w-full" isLoading={isLoading}>
                Log In
            </Button>
        </form>
    );
}
