"use client";

import { useState } from "react";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { createClient } from "@/lib/supabase/client";

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const supabase = createClient();

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    // The callback route will handle the token exchange securely on the server
    const redirectUrl = `${window.location.origin}/auth/callback?type=recovery`;

    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: redirectUrl,
    });

    if (resetError) {
      setError(resetError.message);
    } else {
      setSuccess(true);
    }
    
    setIsLoading(false);
  };

  if (success) {
    return (
      <div className="text-center space-y-4 py-4">
        <div className="mx-auto w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center mb-4">
          <svg className="w-6 h-6 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <h3 className="text-xl font-semibold text-white">Check your email</h3>
        <p className="text-slate-400">
          We've sent password reset instructions to <strong>{email}</strong>.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="text-center mb-6">
        <h3 className="text-lg font-medium text-white">Reset Password</h3>
        <p className="text-sm text-slate-400">Enter your email and we'll send you a link.</p>
      </div>
      
      <form onSubmit={handleReset} className="space-y-4">
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-300">Email Address</label>
          <Input
            type="email"
            placeholder="your.email@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        
        {error && <div className="text-sm text-red-500 bg-red-500/10 p-3 rounded">{error}</div>}
        
        <Button type="submit" className="w-full" isLoading={isLoading}>
          Send Reset Link
        </Button>
      </form>
    </div>
  );
}
