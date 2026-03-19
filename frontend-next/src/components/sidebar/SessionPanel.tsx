import { Button } from "../ui/Button";
import { type SessionResponse } from "@/lib/types";
import { Play, Square, FileText, CheckCircle2, AlertCircle } from "lucide-react";

interface SessionPanelProps {
  session: SessionResponse | null;
  selectedFileIds: number[];
  onCreateSession: () => void;
  onTerminate: () => void;
  onUploadToSession: () => void;
  isCreating: boolean;
  isUploading: boolean;
}

export function SessionPanel({ 
  session, 
  selectedFileIds, 
  onCreateSession, 
  onTerminate, 
  onUploadToSession,
  isCreating,
  isUploading
}: SessionPanelProps) {
  
  if (!session) {
    return (
      <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800">
        <h3 className="text-sm font-semibold text-white mb-2 flex items-center gap-2">
          Workspace Session
          <span className="flex h-2 w-2 relative">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-slate-500 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-slate-500"></span>
          </span>
        </h3>
        <p className="text-xs text-slate-400 mb-4 leading-relaxed">
          Select documents above to create an isolated RAG session for querying.
        </p>
        <Button 
          onClick={onCreateSession} 
          disabled={selectedFileIds.length === 0 || isCreating}
          className="w-full flex items-center justify-center gap-2 bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20 border-transparent hover:border-accent-blue/30"
          size="sm"
          isLoading={isCreating}
        >
          <Play className="w-4 h-4" />
          {isCreating ? "Initializing..." : "Start Session"}
        </Button>
      </div>
    );
  }

  return (
    <div className="bg-accent-blue/10 rounded-xl p-4 border border-accent-blue/20 relative overflow-hidden group">
      {/* Background glow */}
      <div className="absolute top-0 right-0 w-32 h-32 bg-accent-blue/20 blur-3xl -mr-10 -mt-10 rounded-full" />
      
      <div className="relative z-10">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            <div>
              Active Session
              <p className="text-[10px] text-accent-blue font-normal uppercase tracking-wider mt-0.5">Expires in ~{Math.round(session.ttl / 3600)}h</p>
            </div>
          </h3>
          <span className="flex h-2.5 w-2.5 relative">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
          </span>
        </div>

        <div className="bg-navy-950/50 rounded-lg border border-slate-800 p-2 mb-4">
          <p className="text-xs font-medium text-slate-300 mb-2 px-1 text-[10px] uppercase tracking-wider">Loaded Documents</p>
          <ul className="space-y-1 max-h-[120px] overflow-y-auto pr-1">
            {session.files.map((f, i) => (
              <li key={`${f.file_id}-${i}`} className="flex items-center gap-2 text-xs py-1 px-2 rounded bg-slate-900/50">
                <FileText className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                <span className="truncate text-slate-300 flex-1">{f.filename}</span>
                {f.status === "READY" ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                ) : f.status === "FAILED" ? (
                  <AlertCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
                ) : (
                  <div className="w-3.5 h-3.5 border-2 border-slate-400 border-t-accent-blue rounded-full animate-spin shrink-0" />
                )}
              </li>
            ))}
          </ul>
        </div>

        <div className="flex gap-2">
          {selectedFileIds.length > 0 && selectedFileIds.some(id => !session.files.find(f => f.file_id === id)) && (
             <Button 
               onClick={onUploadToSession}
               className="flex-1 bg-white/10 text-white hover:bg-white/20 border-transparent text-xs"
               size="sm"
               isLoading={isUploading}
             >
               Add Selected
             </Button>
          )}
          <Button 
            onClick={onTerminate}
            variant="danger"
            size="sm"
            className="flex-1 flex items-center justify-center gap-1.5 shrink-0 px-2"
          >
            <Square className="w-3.5 h-3.5 fill-current" />
            <span className="text-xs">End</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
