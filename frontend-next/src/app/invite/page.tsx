"use client";

export const dynamic = 'force-dynamic';

import { Suspense, useState, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { verifyInviteToken, acceptInvite } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Scale, ShieldCheck, Mail, User, Lock, ArrowRight } from "lucide-react";
import { createClient } from "@/lib/supabase/client";

function InviteContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams?.get("token");

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [inviteData, setInviteData] = useState<{ org_name: string; email: string; role: string; org_id: string } | null>(null);

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("Invalid or missing invitation token. Please check your email link.");
      setIsLoading(false);
      return;
    }

    verifyInviteToken(token)
      .then((data) => {
        setInviteData(data);
        setIsLoading(false);
      })
      .catch((err) => {
        setError(err.message || "Invitation is invalid or has expired.");
        setIsLoading(false);
      });
  }, [token]);

  const handleAccept = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !password || !fullName) return;

    setIsSubmitting(true);
    setError("");

    try {
      // 1. Call Backend to create user, add to org, and consume token (The Magic Transaction)
      const res = await acceptInvite({ token, full_name: fullName, password });

      // 2. Set the token manually in the local Supabase client so it 
      // immediately treats the user as logged in moving forward.
      const supabase = createClient();
      await supabase.auth.setSession({
        access_token: res.access_token,
        refresh_token: res.access_token, // JWT from backend
      });

      setSuccess(true);
      
      // 3. Seamless redirect
      setTimeout(() => {
        router.push("/chat");
      }, 1500);

    } catch (err: any) {
      setError(err.message || "Failed to accept invitation.");
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#020617] flex items-center justify-center">
        <div className="relative">
          <div className="w-12 h-12 rounded-full border-2 border-slate-800"></div>
          <div className="absolute top-0 w-12 h-12 rounded-full border-t-2 border-blue-500 animate-spin"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#020617] bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-blue-900/10 via-slate-950 to-slate-950 flex flex-col items-center justify-center p-6 sm:p-8">
      {/* Background Decorative Elements */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-[25%] -left-[10%] w-[50%] h-[50%] bg-blue-600/5 blur-[120px] rounded-full"></div>
        <div className="absolute -bottom-[25%] -right-[10%] w-[50%] h-[50%] bg-indigo-600/5 blur-[120px] rounded-full"></div>
      </div>

      <div className="w-full max-w-[440px] relative z-10 transition-all duration-500 animate-in fade-in slide-in-from-bottom-4">
        {/* Brand Header */}
        <div className="mb-10 text-center">
          <div className="inline-flex items-center justify-center p-3 rounded-2xl bg-gradient-to-br from-blue-500/10 to-indigo-500/10 border border-blue-500/20 shadow-2xl shadow-blue-500/10 mb-6 group hover:scale-105 transition-transform duration-300">
            <Scale className="w-8 h-8 text-blue-400 group-hover:text-blue-300" strokeWidth={1.5} />
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-white mb-2 bg-clip-text text-transparent bg-gradient-to-r from-white via-white to-slate-400">
            Legal AI Copilot
          </h1>
          <p className="text-slate-400 font-medium">Enterprise Legal Intelligence</p>
        </div>

        <Card className="border-slate-800/60 bg-slate-900/40 backdrop-blur-xl shadow-2xl overflow-hidden">
          <CardContent className="p-8">
            {error && !inviteData ? (
              <div className="text-center space-y-6 py-4">
                <div className="mx-auto w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-2 border border-red-500/20 animate-pulse">
                  <ShieldCheck className="w-8 h-8 text-red-500/80" />
                </div>
                <div className="space-y-2">
                  <h3 className="text-xl font-bold text-white">Access Denied</h3>
                  <p className="text-slate-400 text-sm leading-relaxed">{error}</p>
                </div>
                <Button 
                  onClick={() => router.push("/login")} 
                  variant="outline"
                  className="w-full border-slate-700 hover:bg-slate-800 text-slate-200"
                >
                  Return to Login
                </Button>
              </div>
            ) : success ? (
              <div className="text-center space-y-6 py-8 animate-in zoom-in-95 duration-500">
                <div className="mx-auto w-20 h-20 rounded-full bg-emerald-500/10 flex items-center justify-center mb-2 border border-emerald-500/20 shadow-[0_0_40px_rgba(16,185,129,0.1)]">
                  <ShieldCheck className="w-10 h-10 text-emerald-500" />
                </div>
                <div className="space-y-2">
                  <h3 className="text-2xl font-bold text-white">Welcome Aboard!</h3>
                  <p className="text-slate-400">
                    Joining <span className="text-blue-400 font-semibold">{inviteData?.org_name}</span>...
                  </p>
                </div>
                <div className="flex justify-center">
                  <div className="flex space-x-1">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-bounce [animation-delay:-0.3s]"></div>
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-bounce [animation-delay:-0.15s]"></div>
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-bounce"></div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-8">
                <div className="text-center space-y-2">
                  <h3 className="text-xl font-bold text-white tracking-tight">Exclusive Invitation</h3>
                  <p className="text-sm text-slate-400 leading-relaxed">
                    You have been invited to join the <span className="text-blue-400 font-semibold">{inviteData?.org_name}</span> legal workspace.
                  </p>
                </div>

                <form onSubmit={handleAccept} className="space-y-5">
                  <div className="space-y-2.5">
                    <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider ml-1">Work Email</label>
                    <div className="relative group">
                      <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                        <Mail className="h-4 w-4 text-slate-500 group-focus-within:text-blue-500 transition-colors" />
                      </div>
                      <Input
                        type="email"
                        value={inviteData?.email}
                        readOnly
                        className="pl-10 h-12 bg-slate-950/50 border-slate-800 text-slate-400 cursor-default focus:ring-0 focus:border-slate-800"
                      />
                    </div>
                  </div>

                  <div className="space-y-2.5">
                    <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider ml-1">Full Name</label>
                    <div className="relative group">
                      <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                        <User className="h-4 w-4 text-slate-500 group-focus-within:text-blue-500 transition-colors" />
                      </div>
                      <Input
                        placeholder="Counselor Name"
                        value={fullName}
                        onChange={(e) => setFullName(e.target.value)}
                        required
                        autoComplete="name"
                        className="pl-10 h-12 bg-slate-950/50 border-slate-800 transition-all hover:border-slate-700 focus:border-blue-500/50 focus:ring-blue-500/20 text-white placeholder:text-slate-600"
                      />
                    </div>
                  </div>

                  <div className="space-y-2.5">
                    <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider ml-1">Security Credentials</label>
                    <div className="relative group">
                      <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                        <Lock className="h-4 w-4 text-slate-500 group-focus-within:text-blue-500 transition-colors" />
                      </div>
                      <Input
                        type="password"
                        placeholder="••••••••••••"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                        minLength={8}
                        autoComplete="new-password"
                        className="pl-10 h-12 bg-slate-950/50 border-slate-800 transition-all hover:border-slate-700 focus:border-blue-500/50 focus:ring-blue-500/20 text-white placeholder:text-slate-600"
                      />
                    </div>
                  </div>

                  <Button 
                    type="submit" 
                    className="w-full h-12 mt-4 bg-blue-600 hover:bg-blue-500 text-white font-bold transition-all shadow-lg shadow-blue-600/20 group"
                    isLoading={isSubmitting}
                  >
                    Complete Registration <ArrowRight className="ml-2 h-4 w-4 group-hover:translate-x-1 transition-transform" />
                  </Button>

                  <p className="text-center text-[10px] text-slate-600 px-4 leading-relaxed">
                    By accepting this invitation, you agree to the Terms of Service and Privacy Policy of the Legal AI Copilot platform.
                  </p>
                </form>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function InvitePage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-[#020617] flex items-center justify-center">
        <div className="w-10 h-10 rounded-full border-2 border-slate-800 border-t-blue-500 animate-spin"></div>
      </div>
    }>
      <InviteContent />
    </Suspense>
  );
}
