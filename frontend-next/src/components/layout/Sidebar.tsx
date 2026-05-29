"use client";

import { useState } from "react";
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
  Users,
  X,
  Trash2,
  Mail,
} from "lucide-react";
import { clsx } from "clsx";
import { createClient } from "@/lib/supabase/client";
import { inviteByEmail, getMembers, removeMember } from "@/lib/api";
import type { User, OrgMember } from "@/lib/types";

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

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteStatus, setInviteStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [inviteMsg, setInviteMsg] = useState("");
  const [showMembersModal, setShowMembersModal] = useState(false);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail) return;
    if (user.app_role !== "ADMIN") {
      setInviteStatus("error");
      setInviteMsg("Only admins can invite users.");
      return;
    }

    setInviteStatus("loading");
    try {
      const res: any = await inviteByEmail(token, inviteEmail, user.org_slug);
      setInviteStatus("success");
      setInviteEmail("");

      if (res.already_registered && res.invite_link) {
        setInviteMsg(`User exists. Share this link: ${res.invite_link}`);
      } else {
        setInviteMsg("Invite sent successfully!");
        setTimeout(() => {
          setInviteStatus("idle");
          setInviteMsg("");
        }, 3000);
      }
    } catch (err: any) {
      setInviteStatus("error");
      setInviteMsg(err.message || "Failed to send invite");
      setTimeout(() => {
        setInviteStatus("idle");
        setInviteMsg("");
      }, 4000);
    }
  };

  const fetchMembers = async () => {
    setMembersLoading(true);
    try {
      const data = await getMembers(token, user.org_slug);
      setMembers(data);
    } catch (err) {
      console.error("Failed to fetch members", err);
    } finally {
      setMembersLoading(false);
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!confirm("Are you sure you want to remove this member?")) return;
    try {
      await removeMember(token, userId, user.org_slug);
      setMembers((prev) => prev.filter((m) => m.user_id !== userId));
    } catch (err: any) {
      alert(err.message || "Failed to remove member");
    }
  };

  const openMembersModal = () => {
    setShowMembersModal(true);
    fetchMembers();
  };

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
  }

  function isActive(href: string) {
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <>
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
            <div className="min-w-0 flex-1 mr-2">
              <p className="text-xs font-medium text-[#F0EEE9] truncate">
                {user.org_name || user.org_slug}
              </p>
              <p className="text-[10px] text-[#7A7A8A] truncate">{user.email}</p>
            </div>
            {user.app_role === "ADMIN" ? (
              <button
                onClick={openMembersModal}
                className="p-1 rounded bg-[#2A2A32] hover:bg-[#3A3A45] text-[#D4A853] hover:text-[#F0EEE9] transition-colors shrink-0"
                title="Manage Members"
              >
                <Users className="w-3.5 h-3.5" />
              </button>
            ) : (
              <ChevronRight className="w-3.5 h-3.5 text-[#4A4A5A] shrink-0" />
            )}
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

        {/* Invite Colleague Panel (Admins only) */}
        {user.app_role === "ADMIN" && (
          <div className="px-4 py-3.5 border-t border-[#2A2A32] bg-[#16161D]/30 shrink-0">
            <p className="text-[10px] uppercase tracking-widest text-[#4A4A5A] font-semibold mb-2">
              Invite Colleague
            </p>
            <form onSubmit={handleInvite} className="flex gap-1.5">
              <input
                type="email"
                placeholder="colleague@firm.com"
                className="flex-1 min-w-0 bg-[#0E0E12] border border-[#2A2A32] rounded-md px-2 py-1 text-xs text-[#F0EEE9] placeholder-[#4A4A5A] focus:outline-none focus:border-[#D4A853] transition-colors"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                required
              />
              <button
                type="submit"
                className="h-[26px] w-[26px] rounded-md shrink-0 bg-[#2A2A32] hover:bg-[#3A3A45] border border-[#3A3A45] flex items-center justify-center text-[#D4A853] hover:text-[#F0EEE9] transition-colors"
                disabled={inviteStatus === "loading" || !inviteEmail}
              >
                <Mail className="w-3.5 h-3.5" />
              </button>
            </form>
            {inviteMsg && (
              <p
                className={`text-[9px] mt-1.5 leading-tight font-medium ${
                  inviteStatus === "success" ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {inviteMsg}
              </p>
            )}
          </div>
        )}

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

      {/* Members Modal (Admins only) */}
      {showMembersModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-[#0E0E12]/80 backdrop-blur-sm">
          <div className="w-full max-w-md bg-[#16161D] border border-[#2A2A32] rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[80vh]">
            <div className="p-4 border-b border-[#2A2A32] flex items-center justify-between bg-[#16161D]">
              <h3 className="text-[#F0EEE9] font-semibold text-sm flex items-center gap-2">
                <Users className="w-4 h-4 text-[#D4A853]" />
                Organization Members
              </h3>
              <button
                onClick={() => setShowMembersModal(false)}
                className="text-[#7A7A8A] hover:text-[#F0EEE9] transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {membersLoading ? (
                <div className="py-8 text-center text-[#7A7A8A] text-sm">
                  Loading members...
                </div>
              ) : members.length === 0 ? (
                <div className="py-8 text-center text-[#7A7A8A] text-sm">
                  No other members found.
                </div>
              ) : (
                members.map((member) => (
                  <div
                    key={member.user_id}
                    className="flex items-center justify-between p-3 rounded-lg bg-[#0E0E12]/40 border border-[#2A2A32]"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-[#F0EEE9] truncate">
                        {member.full_name || "New Colleague"}
                      </p>
                      <p className="text-xs text-[#7A7A8A] truncate">
                        {member.email}
                      </p>
                      <span
                        className={`inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded mt-1 ${
                          member.role === "ADMIN"
                            ? "bg-[#D4A853]/10 text-[#D4A853] border border-[#D4A853]/20"
                            : "bg-[#2A2A32] text-[#7A7A8A]"
                        }`}
                      >
                        {member.role}
                      </span>
                    </div>
                    {member.user_id !== user.sub && (
                      <button
                        onClick={() => handleRemoveMember(member.user_id)}
                        className="p-2 text-[#7A7A8A] hover:text-[#F06B6B] hover:bg-[#F06B6B]/10 rounded-lg transition-colors"
                        title="Remove Member"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>

            <div className="p-4 border-t border-[#2A2A32] bg-[#16161D]">
              <p className="text-[10px] text-[#4A4A5A] text-center font-medium">
                Admins can manage organization access and permissions.
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
