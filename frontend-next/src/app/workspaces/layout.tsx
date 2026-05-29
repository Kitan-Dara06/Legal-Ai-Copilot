"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { getMe } from "@/lib/api";
import { Sidebar } from "@/components/layout/Sidebar";
import { Loader2 } from "lucide-react";
import type { User } from "@/lib/types";

export default function WorkspacesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router   = useRouter();
  const supabase = createClient();

  const [user,  setUser]  = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    // Initial session load
    supabase.auth.getSession().then(async ({ data }: any) => {
      if (!data?.session) { router.push("/login"); return; }

      const accessToken = data.session.access_token;
      const orgSlug     = data.session.user?.user_metadata?.org_slug;
      setToken(accessToken);

      try {
        const me = await getMe(accessToken, orgSlug);
        setUser(me);
      } catch {
        // Fallback: build minimal user from Supabase session metadata
        setUser({
          sub:       data.session.user.id,
          email:     data.session.user.email ?? "",
          role:      "authenticated",
          aud:       "authenticated",
          org_id:    data.session.user?.user_metadata?.org_id   ?? "",
          org_slug:  orgSlug ?? "",
          org_name:  data.session.user?.user_metadata?.org_name ?? orgSlug ?? "",
          app_role:  data.session.user?.user_metadata?.app_role ?? "MEMBER",
        });
      }
    });

    // Keep token fresh — fires whenever Supabase silently refreshes it
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event: string, session: any) => {
        if (!session) { router.push("/login"); return; }
        setToken(session.access_token);
      },
    );

    return () => subscription.unsubscribe();
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar — shows skeleton while user resolves */}
      {user && token ? (
        <Sidebar user={user} token={token} />
      ) : (
        <div className="w-[240px] shrink-0 h-screen bg-[#0E0E12] border-r border-[#2A2A32] flex items-center justify-center">
          <Loader2 className="w-5 h-5 text-[#D4A853] animate-spin" />
        </div>
      )}

      {/* Page content */}
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
