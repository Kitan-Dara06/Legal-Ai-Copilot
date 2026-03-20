import { useState, useEffect, useRef } from "react";
import { LogOut, ChevronUp, Check } from "lucide-react";
import { type User, type OrgEntry } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { createClient } from "@/lib/supabase/client";
import { getMyOrgs } from "@/lib/api";

interface UserInfoProps {
    user: User;
    token: string;
    onSwitchOrg: (orgSlug: string) => void;
}

export function UserInfo({ user, token, onSwitchOrg }: UserInfoProps) {
    const [orgs, setOrgs] = useState<OrgEntry[]>([]);
    const [isMenuOpen, setIsMenuOpen] = useState(false);
    const menuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!token) return;
        getMyOrgs(token)
            .then(setOrgs)
            .catch((err) => console.error("Failed to load orgs", err));
    }, [token]);

    // Close menu when clicking outside
    useEffect(() => {
        function handleClickOutside(e: MouseEvent) {
            if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
                setIsMenuOpen(false);
            }
        }
        if (isMenuOpen) {
            document.addEventListener("mousedown", handleClickOutside);
        }
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, [isMenuOpen]);

    const handleLogout = async () => {
        const supabase = createClient();
        await supabase.auth.signOut();
        window.location.href = "/login";
    };

    return (
        <div className="mt-auto border-t border-slate-800 p-4 shrink-0 bg-navy-950/80 backdrop-blur pb-6 relative">
            {/* Context Menu Popup */}
            {isMenuOpen && (
                <div
                    ref={menuRef}
                    className="absolute bottom-full left-4 right-4 mb-2 bg-slate-900 border border-slate-700 rounded-lg shadow-2xl p-2 z-50 flex flex-col gap-1"
                >
                    <div className="px-2 py-1.5 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                        Switch Workspace
                    </div>
                    <div className="max-h-[200px] overflow-y-auto hide-scrollbar space-y-1">
                        {orgs.map((org) => (
                            <button
                                key={org.org_slug}
                                onClick={() => {
                                    onSwitchOrg(org.org_slug);
                                    setIsMenuOpen(false);
                                }}
                                className={`flex items-center justify-between w-full px-2 py-2 text-sm rounded transition-colors ${
                                    org.org_slug === user.org_slug
                                        ? "bg-accent-blue/10 text-accent-blue"
                                        : "text-slate-300 hover:bg-slate-800"
                                }`}
                            >
                                <span className="truncate">{org.org_name || org.org_slug}</span>
                                {org.org_slug === user.org_slug && (
                                    <Check className="w-3.5 h-3.5 shrink-0" />
                                )}
                            </button>
                        ))}
                        {orgs.length === 0 && (
                            <div className="px-2 py-2 text-sm text-slate-500 text-center">
                                No other workspaces
                            </div>
                        )}
                    </div>
                    <div className="my-1 border-t border-slate-800" />
                    <button
                        onClick={handleLogout}
                        className="flex items-center justify-between w-full px-2 py-2 text-sm text-red-400 hover:bg-red-400/10 hover:text-red-300 rounded transition-colors"
                    >
                        <span>Log Out</span>
                        <LogOut className="w-3.5 h-3.5 shrink-0" />
                    </button>
                </div>
            )}

            <div className="flex flex-col gap-4">
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        setIsMenuOpen(!isMenuOpen);
                    }}
                    className="flex w-full items-center gap-3 p-2 -m-2 rounded-lg hover:bg-slate-800/50 transition-colors text-left focus:outline-none"
                >
                    <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center shrink-0 border border-slate-700">
                        <span className="font-semibold text-sm text-slate-300">
                            {user.email.substring(0, 2).toUpperCase()}
                        </span>
                    </div>
                    <div className="flex-1 min-w-0 flex items-center justify-between">
                        <div className="min-w-0 pr-2">
                            <div className="flex items-center gap-2 mb-0.5">
                                <p className="text-sm font-medium text-slate-200 truncate">
                                    {user.org_name || user.org_slug}
                                </p>
                                {user.app_role === "ADMIN" && (
                                    <Badge
                                        variant="warning"
                                        className="px-1.5 py-0 text-[10px] uppercase tracking-wider shrink-0 bg-accent-gold/20 text-accent-gold border-accent-gold/20"
                                    >
                                        Admin
                                    </Badge>
                                )}
                            </div>
                            <p className="text-xs text-slate-400 truncate">{user.email}</p>
                        </div>
                        <ChevronUp
                            className={`w-4 h-4 text-slate-500 shrink-0 transition-transform duration-200 ${isMenuOpen ? "rotate-180" : ""}`}
                        />
                    </div>
                </button>
            </div>
        </div>
    );
}

