import { ShieldCheck, LogOut, Settings, Users } from "lucide-react";
import { type User } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { createClient } from "@/lib/supabase/client";

interface UserInfoProps {
  user: User;
}

export function UserInfo({ user }: UserInfoProps) {
  const handleLogout = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    window.location.href = "/login";
  };

  return (
    <div className="mt-auto border-t border-slate-800 p-4 shrink-0 bg-navy-950/80 backdrop-blur pb-6">
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center shrink-0 border border-slate-700">
            <span className="font-semibold text-sm text-slate-300">
              {user.email.substring(0, 2).toUpperCase()}
            </span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <p className="text-sm font-medium text-slate-200 truncate">{user.org_name || user.org_slug}</p>
              {user.app_role === "ADMIN" && (
                <Badge variant="warning" className="px-1.5 py-0 text-[10px] uppercase tracking-wider shrink-0 bg-accent-gold/20 text-accent-gold border-accent-gold/20">Admin</Badge>
              )}
            </div>
            <p className="text-xs text-slate-400 truncate">{user.email}</p>
          </div>
        </div>

        <div className="flex bg-slate-900 rounded-lg p-1 border border-slate-800 relative z-50">
          <button 
            onClick={handleLogout}
            className="flex flex-1 items-center justify-center gap-2 px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-white rounded transition-colors hover:bg-slate-800/50"
          >
            <LogOut className="w-3.5 h-3.5" />
            Log Out
          </button>
        </div>
      </div>
    </div>
  );
}
