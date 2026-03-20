import { Scale, Menu } from "lucide-react";

interface TopBarProps {
    sessionActive: boolean;
    onMenuClick?: () => void;
}

export function TopBar({ sessionActive, onMenuClick }: TopBarProps) {
    return (
        <div className="h-16 border-b border-slate-800 bg-navy-950/80 backdrop-blur sticky top-0 z-20 px-4 md:px-6 flex items-center justify-between w-full">
            <div className="flex items-center gap-3">
                <button 
                    onClick={onMenuClick}
                    className="p-2 -ml-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors"
                    aria-label="Toggle Sidebar"
                >
                    <Menu className="w-5 h-5" />
                </button>
                {sessionActive ? (
                    <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 px-3 py-1.5 rounded-full text-xs font-semibold">
                        <span className="flex h-2 w-2 relative">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                        </span>
                        <span className="hidden sm:inline">Active Session</span>
                        <span className="sm:hidden">Active</span>
                    </div>
                ) : (
                    <div className="flex items-center gap-2 bg-slate-800/50 border border-slate-700/50 text-slate-400 px-3 py-1.5 rounded-full text-xs font-semibold">
                        <span className="w-2 h-2 rounded-full bg-slate-500"></span>
                        <span className="hidden sm:inline">No Active Session</span>
                        <span className="sm:hidden">No Session</span>
                    </div>
                )}
            </div>

            <div className="flex items-center gap-4">
                <div className="flex flex-col items-end px-2">
                    <span className="text-sm font-semibold text-white">
                        Agentic Mode
                    </span>
                    <span className="text-[10px] text-emerald-400">
                        Always active
                    </span>
                </div>
            </div>
        </div>
    );
}
