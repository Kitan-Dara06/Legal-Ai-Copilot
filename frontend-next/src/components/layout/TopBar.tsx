import { Scale } from "lucide-react";

interface TopBarProps {
    sessionActive: boolean;
}

export function TopBar({ sessionActive }: TopBarProps) {
    return (
        <div className="h-16 border-b border-slate-800 bg-navy-950/80 backdrop-blur fixed top-0 right-0 left-[320px] z-20 px-6 flex items-center justify-between">
            <div className="flex items-center gap-3">
                {sessionActive ? (
                    <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 px-3 py-1.5 rounded-full text-xs font-semibold">
                        <span className="flex h-2 w-2 relative">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                        </span>
                        Active Session
                    </div>
                ) : (
                    <div className="flex items-center gap-2 bg-slate-800/50 border border-slate-700/50 text-slate-400 px-3 py-1.5 rounded-full text-xs font-semibold">
                        <span className="w-2 h-2 rounded-full bg-slate-500"></span>
                        No Active Session
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
