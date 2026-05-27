"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BookOpen,
  Clock,
  AlertTriangle,
  Settings,
  Scale,
  LogOut,
  Bell,
  ChevronRight,
} from "lucide-react";
import { clsx } from "clsx";
import { createClient } from "@/lib/supabase/client";
import type { User } from "@/lib/types";

interface SidebarProps {
  user: User;
  token: string;
  unreadCount?: number;
}

const NAV_ITEMS = [
  { href: "/workspaces", label: "Workspaces", icon: BookOpen },
  { href: "/deadlines",  label: "Deadlines",  icon: Clock },
  { href: "/escalations",label: "Escalations",icon: AlertTriangle },
];

const ADMIN_ITEMS = [
  { href: "/admin",   label: "Admin",    icon: Settings },
];

export function Sidebar({ user, token, unreadCount = 0 }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
  }

  function isActive(href: string) {
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <aside className="w-[240px] shrink-0 h-screen sticky top-0 flex flex-col bg-[#0E0E12] border-r border-[#2A2A32] overflow-hidden">

      {/* Brand */}
      <div className="px-5 pt-6 pb-5 border-b border-[#2A2A32]">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#D4A853]/10 border border-[#D4A853]/25 flex items-center justify-center">
            <Scale className="w-4 h-4 text-[#D4A853]" strokeWidth={1.5} />
          </div>
          <div>
            <span className="text-[15px] font-semibold text-[#F0EEE9] tracking-tight">Lex</span>
            <p className="text-[10px] text-[#7A7A8A] uppercase tracking-widest leading-none mt-0.5">
              AI Copilot
            </p>
          </div>
        </div>
      </div>

      {/* Org Badge */}
      <div className="px-4 py-3 border-b border-[#2A2A32]">
        <div className="flex items-center justify-between px-2 py-1.5 rounded-lg bg-[#16161D] border border-[#2A2A32]">
          <div className="min-w-0">
            <p className="text-xs font-medium text-[#F0EEE9] truncate">
              {user.org_name || user.org_slug}
            </p>
            <p className="text-[10px] text-[#7A7A8A] truncate">{user.email}</p>
          </div>
          <ChevronRight className="w-3.5 h-3.5 text-[#4A4A5A] shrink-0" />
        </div>
      </div>

      {/* Primary Nav */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={clsx(
              "group flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-150",
              isActive(href)
                ? "bg-[#D4A853]/10 text-[#D4A853] border-l-2 border-[#D4A853] pl-[10px]"
                : "text-[#7A7A8A] hover:text-[#F0EEE9] hover:bg-[#16161D] border-l-2 border-transparent",
            )}
          >
            <Icon className="w-4 h-4 shrink-0" strokeWidth={1.5} />
            {label}
          </Link>
        ))}

        {/* Divider */}
        <div className="pt-3 pb-2">
          <p className="px-3 text-[10px] uppercase tracking-widest text-[#4A4A5A] font-medium">
            System
          </p>
        </div>

        {user.app_role === "ADMIN" &&
          ADMIN_ITEMS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={clsx(
                "group flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-150",
                isActive(href)
                  ? "bg-[#7C6AF7]/10 text-[#7C6AF7] border-l-2 border-[#7C6AF7] pl-[10px]"
                  : "text-[#7A7A8A] hover:text-[#F0EEE9] hover:bg-[#16161D] border-l-2 border-transparent",
              )}
            >
              <Icon className="w-4 h-4 shrink-0" strokeWidth={1.5} />
              {label}
            </Link>
          ))}
      </nav>

      {/* Bottom — Notifications + User */}
      <div className="px-3 pb-4 pt-2 border-t border-[#2A2A32] space-y-0.5">
        <Link
          href="/notifications"
          className={clsx(
            "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all text-[#7A7A8A] hover:text-[#F0EEE9] hover:bg-[#16161D]",
          )}
        >
          <div className="relative">
            <Bell className="w-4 h-4" strokeWidth={1.5} />
            {unreadCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 w-3.5 h-3.5 rounded-full bg-[#D4A853] text-[#0E0E12] text-[8px] font-bold flex items-center justify-center">
                {unreadCount > 9 ? "9+" : unreadCount}
              </span>
            )}
          </div>
          Notifications
          {unreadCount > 0 && (
            <span className="ml-auto text-[10px] font-semibold text-[#D4A853]">
              {unreadCount}
            </span>
          )}
        </Link>

        <button
          onClick={handleSignOut}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[#7A7A8A] hover:text-[#F06B6B] hover:bg-[#F06B6B]/5 transition-all"
        >
          <LogOut className="w-4 h-4" strokeWidth={1.5} />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
