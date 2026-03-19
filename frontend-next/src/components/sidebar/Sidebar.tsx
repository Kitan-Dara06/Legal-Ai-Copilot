import { useState, useRef, useEffect } from "react";
import { type User, type FileItem, type SessionResponse } from "@/lib/types";
import { FileList } from "./FileList";
import { FileUpload } from "./FileUpload";
import { SessionPanel } from "./SessionPanel";
import { UserInfo } from "./UserInfo";
import { Scale, Users, Mail } from "lucide-react";
import { inviteByEmail } from "@/lib/api";
import { Button } from "../ui/Button";

interface SidebarProps {
  user: User;
  onUploadSuccess: () => void;
  files: FileItem[];
  filesLoading: boolean;
  onDeleteFile: (id: number) => void;
  session: SessionResponse | null;
  onCreateSession: (fileIds: number[]) => void;
  onTerminate: () => void;
  onUploadToSession: (fileIds: number[]) => void;
  isCreatingSession: boolean;
  isUploadingToSession: boolean;
}

export function Sidebar({
  user,
  onUploadSuccess,
  files,
  filesLoading,
  onDeleteFile,
  session,
  onCreateSession,
  onTerminate,
  onUploadToSession,
  isCreatingSession,
  isUploadingToSession
}: SidebarProps) {
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteStatus, setInviteStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [inviteMsg, setInviteMsg] = useState("");

  const handleToggleSelection = (id: number) => {
    setSelectedFileIds(prev => 
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail || user.app_role !== "ADMIN") return;
    
    setInviteStatus("loading");
    try {
      // Assuming invite token is available in context or via api client
      const fakeTokenForNow = "todo-pass-token-from-useAuth-hook";
      await inviteByEmail(fakeTokenForNow, inviteEmail, user.org_slug);
      setInviteStatus("success");
      setInviteEmail("");
      setInviteMsg("Invite sent successfully!");
      setTimeout(() => setInviteStatus("idle"), 3000);
    } catch (err: any) {
      setInviteStatus("error");
      setInviteMsg(err.message || "Failed to send invite");
    }
  };

  return (
    <div className="w-[320px] shrink-0 bg-navy-950 border-r border-slate-800 flex flex-col h-screen fixed left-0 top-0 z-20 overflow-hidden shadow-2xl">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 flex items-center justify-between bg-navy-950 sticky top-0 z-10">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-accent-blue/10 border border-accent-blue/20 flex items-center justify-center">
            <Scale className="w-5 h-5 text-accent-blue" strokeWidth={1.5} />
          </div>
          <div>
            <h1 className="font-semibold text-white tracking-tight">{user.org_name || user.org_slug}</h1>
            <p className="text-[10px] text-slate-400 uppercase tracking-widest font-medium">Copilot Workspace</p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto flex flex-col hide-scrollbar">
        <div className="p-4 space-y-6">
          
          {/* Active Session Panel */}
          <section>
            <SessionPanel 
              session={session}
              selectedFileIds={selectedFileIds}
              onCreateSession={() => onCreateSession(selectedFileIds)}
              onTerminate={() => {
                onTerminate();
                setSelectedFileIds([]); // Clear selection when terminating
              }}
              onUploadToSession={() => onUploadToSession(selectedFileIds)}
              isCreating={isCreatingSession}
              isUploading={isUploadingToSession}
            />
          </section>

          {/* Upload Area */}
          <section>
            <FileUpload 
              onUploadSuccess={onUploadSuccess} 
              orgSlug={user.org_slug} 
            />
          </section>

          {/* Document Library */}
          <section className="flex-1 flex flex-col min-h-[300px]">
            <div className="flex items-center justify-between mb-3 px-1">
              <h2 className="text-sm font-semibold text-white">Document Library</h2>
              <span className="text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full font-medium">
                {files.length}
              </span>
            </div>
            <FileList 
              files={files}
              selectedIds={selectedFileIds}
              onToggleSelection={handleToggleSelection}
              onDeleteFile={onDeleteFile}
              isLoading={filesLoading}
            />
          </section>
        </div>
      </div>

      {user.app_role === "ADMIN" && (
        <div className="px-4 pb-2 border-t border-slate-800/50 bg-navy-950/80 backdrop-blur pt-4 shrink-0">
          <form onSubmit={handleInvite} className="flex gap-2">
            <input 
              type="email" 
              placeholder="Invite colleague (email)"
              className="flex-1 min-w-0 bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-accent-blue transition-colors"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              required
            />
            <Button 
              type="submit" 
              size="icon" 
              className="h-[30px] w-[30px] rounded shrink-0 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300"
              disabled={inviteStatus === "loading" || !inviteEmail}
            >
              <Mail className="w-3.5 h-3.5" />
            </Button>
          </form>
          {inviteMsg && (
            <p className={`text-[10px] mt-1.5 ${inviteStatus === "success" ? "text-emerald-400" : "text-red-400"}`}>
              {inviteMsg}
            </p>
          )}
        </div>
      )}

      {/* User Info (Pinned to bottom) */}
      <UserInfo user={user} />
    </div>
  );
}
