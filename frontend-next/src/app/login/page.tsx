"use client";

export const dynamic = 'force-dynamic';

import { Suspense, useState, useEffect } from "react";
import { LoginForm } from "@/components/auth/LoginForm";
import { SignupForm } from "@/components/auth/SignupForm";
import { ForgotPasswordForm } from "@/components/auth/ForgotPasswordForm";
import { Card, CardContent } from "@/components/ui/Card";
import { Scale } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

function LoginContent() {
  const searchParams = useSearchParams();
  const [activeTab, setActiveTab] = useState<"login" | "signup" | "forgot" | "recovery">("login");
  const [newPassword, setNewPassword] = useState("");
  const [isRecovering, setIsRecovering] = useState(false);
  const [error, setError] = useState("");

  const supabase = createClient();

  useEffect(() => {
    // If we're redirected back from auth/callback with a recovery flag
    if (searchParams?.get("type") === "recovery") {
      setActiveTab("recovery");
    }
  }, [searchParams]);

  const handleUpdatePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsRecovering(true);
    setError("");

    const { error } = await supabase.auth.updateUser({
      password: newPassword,
    });

    if (error) {
      setError(error.message);
      setIsRecovering(false);
    } else {
      window.location.href = "/chat";
    }
  };

  return (
    <div className="min-h-screen bg-navy-950 flex flex-col items-center justify-center p-4">
      {/* Branding */}
      <div className="mb-8 text-center flex flex-col items-center">
        <div className="bg-accent-blue/10 p-3 rounded-2xl mb-4 border border-accent-blue/20 shadow-[0_0_30px_rgba(59,130,246,0.15)]">
          <Scale className="w-10 h-10 text-accent-blue" strokeWidth={1.5} />
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-white mb-2">Legal AI Copilot</h1>
        <p className="text-slate-400 max-w-sm">
          Securely chat with your corporate contracts and legal documents.
        </p>
      </div>

      <Card className="w-full max-w-md border-slate-800/50 bg-slate-900/50">
        <CardContent className="pt-6">
          {activeTab === "recovery" ? (
            <div className="space-y-4">
              <div className="text-center mb-6">
                <h3 className="text-lg font-medium text-white">Set New Password</h3>
                <p className="text-sm text-emerald-400">Your email has been verified.</p>
              </div>
              <form onSubmit={handleUpdatePassword} className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-slate-300">New Password</label>
                  <input
                    type="password"
                    key="recovery_new_pw"
                    className="flex h-10 w-full rounded-md border text-sm px-3 py-2 glass-input focus:glass-input-focus text-white border-slate-800"
                    placeholder="••••••••"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    minLength={8}
                  />
                </div>
                {error && <div className="text-sm text-red-500">{error}</div>}
                
                <button 
                  type="submit" 
                  disabled={isRecovering}
                  className="w-full inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors h-10 px-4 bg-accent-blue text-white hover:bg-accent-blue-dark active:scale-95 disabled:opacity-50"
                >
                  {isRecovering ? "Updating..." : "Update Password & Login"}
                </button>
              </form>
            </div>
          ) : activeTab === "forgot" ? (
            <>
              <ForgotPasswordForm />
              <div className="mt-6 text-center">
                <button
                  type="button"
                  onClick={() => setActiveTab("login")}
                  className="text-sm text-slate-400 hover:text-white transition-colors"
                >
                  Back to login
                </button>
              </div>
            </>
          ) : (
            <>
              {/* Tabs */}
              <div className="flex border-b border-slate-800 mb-6">
                <button
                  className={`flex-1 pb-3 text-sm font-medium transition-colors border-b-2 ${
                    activeTab === "login"
                      ? "border-accent-blue text-white"
                      : "border-transparent text-slate-400 hover:text-slate-300"
                  }`}
                  onClick={() => setActiveTab("login")}
                >
                  Log In
                </button>
                <button
                  className={`flex-1 pb-3 text-sm font-medium transition-colors border-b-2 ${
                    activeTab === "signup"
                      ? "border-accent-blue text-white"
                      : "border-transparent text-slate-400 hover:text-slate-300"
                  }`}
                  onClick={() => setActiveTab("signup")}
                >
                  Sign Up
                </button>
              </div>

              {activeTab === "login" ? <LoginForm /> : <SignupForm />}

              {activeTab === "login" && (
                <div className="mt-6 text-center">
                  <button
                    type="button"
                    onClick={() => setActiveTab("forgot")}
                    className="text-sm text-slate-400 hover:text-white transition-colors"
                  >
                    Forgot your password?
                  </button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
      
      <div className="mt-8 text-center text-xs text-slate-500">
        <p>By signing in, you agree to our Terms of Service and Privacy Policy.</p>
        <p className="mt-1">Protected by AES-256 encryption.</p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-navy-950 flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-accent-blue border-t-transparent animate-spin"></div>
      </div>
    }>
      <LoginContent />
    </Suspense>
  );
}
