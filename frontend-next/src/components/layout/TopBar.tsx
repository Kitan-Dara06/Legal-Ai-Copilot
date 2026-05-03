"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Scale, Menu, MessageSquare, Search } from "lucide-react";

interface TopBarProps {
    sessionActive?: boolean;
    onMenuClick?: () => void;
    mode?: "chat" | "due-diligence";
}

export function TopBar({ sessionActive, onMenuClick, mode }: TopBarProps) {
    const pathname = usePathname();
    const isDueDiligence = mode === "due-diligence" || pathname?.startsWith("/due-diligence");

    return (
        <div className="h-16 border-b border-slate-800 bg-navy-950/80 backdrop-blur sticky top-0 z-20 px-4 md:px-6 flex items-center justify-between w-full flex-shrink-0">
            {/* Left — menu + session badge (only on chat) */}
            <div className="flex items-center gap-3">
                {onMenuClick && (
                    <button
                        onClick={onMenuClick}
                        className="p-2 -ml-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors"
                        aria-label="Toggle Sidebar"
                    >
                        <Menu className="w-5 h-5" />
                    </button>
                )}

                {!isDueDiligence && (
                    sessionActive ? (
                        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 px-3 py-1.5 rounded-full text-xs font-semibold">
                            <span className="flex h-2 w-2 relative">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                            </span>
                            <span className="hidden sm:inline">Active Session</span>
                            <span className="sm:hidden">Active</span>
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 bg-slate-800/50 border border-slate-700/50 text-slate-400 px-3 py-1.5 rounded-full text-xs font-semibold">
                            <span className="w-2 h-2 rounded-full bg-slate-500" />
                            <span className="hidden sm:inline">No Active Session</span>
                            <span className="sm:hidden">No Session</span>
                        </div>
                    )
                )}

                {isDueDiligence && (
                    <div className="flex items-center gap-2 bg-accent-blue/10 border border-accent-blue/20 text-accent-blue px-3 py-1.5 rounded-full text-xs font-semibold">
                        <Scale className="w-3 h-3" />
                        <span>Due Diligence Workspace</span>
                    </div>
                )}
            </div>

            {/* Centre — mode switcher */}
            <div className="absolute left-1/2 -translate-x-1/2 flex items-center bg-slate-900 border border-slate-800 rounded-lg p-0.5">
                <Link
                    href="/chat"
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                        !isDueDiligence
                            ? "bg-slate-700 text-white shadow"
                            : "text-slate-500 hover:text-slate-300"
                    }`}
                >
                    <MessageSquare className="w-3 h-3" />
                    Chat
                </Link>
                <Link
                    href="/due-diligence"
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                        isDueDiligence
                            ? "bg-slate-700 text-white shadow"
                            : "text-slate-500 hover:text-slate-300"
                    }`}
                >
                    <Search className="w-3 h-3" />
                    Due Diligence
                </Link>
            </div>

            {/* Right */}
            <div className="flex items-center gap-2">
                <div className="flex flex-col items-end px-2">
                    <span className="text-sm font-semibold text-white">Agentic Mode</span>
                    <span className="text-[10px] text-emerald-400">Always active</span>
                </div>
            </div>
        </div>
    );
}
