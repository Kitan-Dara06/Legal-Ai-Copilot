import { useState } from "react";
import { type User, type FileItem, type SessionResponse } from "@/lib/types";
import { FileList } from "./FileList";
import { FileUpload } from "./FileUpload";
import { SessionPanel } from "./SessionPanel";
import { UserInfo } from "./UserInfo";
import { Scale, Mail, Users, X, Trash2 } from "lucide-react";
import { inviteByEmail, getMembers, removeMember } from "@/lib/api";
import { Button } from "../ui/Button";
import { type OrgMember } from "@/lib/types";

interface SidebarProps {
    user: User;
    token: string;
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
    onSwitchOrg: (orgSlug: string) => void;
    selectedFileIds: number[];
    onToggleSelection: (id: number) => void;
}

export function Sidebar({
    user,
    token,
    onUploadSuccess,
    files,
    filesLoading,
    onDeleteFile,
    session,
    onCreateSession,
    onTerminate,
    onUploadToSession,
    isCreatingSession,
    isUploadingToSession,
    onSwitchOrg,
    selectedFileIds,
    onToggleSelection,
}: SidebarProps) {
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteStatus, setInviteStatus] = useState<
        "idle" | "loading" | "success" | "error"
    >("idle");
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
            // Note: The backend response type might return already_registered
            const res: any = await inviteByEmail(token, inviteEmail, user.org_slug);
            setInviteStatus("success");
            setInviteEmail("");
            
            if (res.already_registered && res.invite_link) {
                // Keep the success state visible longer so they can copy the link
                setInviteMsg(`User exists. Share this link for them to join:  ${res.invite_link}`);
            } else {
                setInviteMsg("Invite sent successfully!");
                setTimeout(() => setInviteStatus("idle"), 3000);
            }
        } catch (err: any) {
            setInviteStatus("error");
            setInviteMsg(err.message || "Failed to send invite");
            setTimeout(() => setInviteStatus("idle"), 4000);
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

    return (
        <div className="w-[320px] shrink-0 bg-navy-950 border-r border-slate-800 flex flex-col h-full overflow-hidden shadow-2xl">
            {/* Header */}
            <div className="p-4 border-b border-slate-800 flex items-center justify-between bg-navy-950 sticky top-0 z-10">
                <div className="flex items-center gap-2">
                    <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-accent-blue/10 border border-accent-blue/20 flex items-center justify-center">
                            <Scale
                                className="w-5 h-5 text-accent-blue"
                                strokeWidth={1.5}
                            />
                        </div>
                        {user.app_role === "ADMIN" && (
                            <button
                                onClick={openMembersModal}
                                className="w-8 h-8 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 flex items-center justify-center text-slate-400 hover:text-white transition-colors"
                                title="Manage Members"
                            >
                                <Users className="w-4 h-4" />
                            </button>
                        )}
                    </div>
                    <div>
                        <h1 className="font-semibold text-white tracking-tight">
                            {user.org_name || user.org_slug}
                        </h1>
                        <p className="text-[10px] text-slate-400 uppercase tracking-widest font-medium">
                            Copilot Workspace
                        </p>
                    </div>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto flex flex-col hide-scrollbar">
                <div className="p-4 space-y-6">
                    {/* Upload Area */}
                    <section>
                        <FileUpload
                            onUploadSuccess={onUploadSuccess}
                            orgSlug={user.org_slug}
                        />
                    </section>

                    {/* Document Library */}
                    <section className="flex flex-col">
                        <div className="flex items-center justify-between mb-3 px-1">
                            <h2 className="text-sm font-semibold text-white">
                                Document Library
                            </h2>
                            <span className="text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full font-medium">
                                {files.length}
                            </span>
                        </div>
                        <FileList
                            files={files}
                            selectedIds={selectedFileIds}
                            onToggleSelection={onToggleSelection}
                            onDeleteFile={onDeleteFile}
                            isLoading={filesLoading}
                        />
                    </section>

                    {/* Active Session Panel */}
                    <section className="pb-4">
                        <SessionPanel
                            session={session}
                            selectedFileIds={selectedFileIds}
                            onCreateSession={() =>
                                onCreateSession(selectedFileIds)
                            }
                            onTerminate={onTerminate}
                            onUploadToSession={() =>
                                onUploadToSession(selectedFileIds)
                            }
                            isCreating={isCreatingSession}
                            isUploading={isUploadingToSession}
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
                            disabled={
                                inviteStatus === "loading" || !inviteEmail
                            }
                        >
                            <Mail className="w-3.5 h-3.5" />
                        </Button>
                    </form>
                    {inviteMsg && (
                        <p
                            className={`text-[10px] mt-1.5 ${inviteStatus === "success" ? "text-emerald-400" : "text-red-400"}`}
                        >
                            {inviteMsg}
                        </p>
                    )}
                </div>
            )}

            {/* User Info (Pinned to bottom) */}
            <UserInfo user={user} token={token} onSwitchOrg={onSwitchOrg} />

            {/* Members Modal */}
            {showMembersModal && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-navy-950/80 backdrop-blur-sm">
                    <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[80vh]">
                        <div className="p-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/50">
                            <h3 className="text-white font-semibold flex items-center gap-2">
                                <Users className="w-4 h-4 text-accent-blue" />
                                Organization Members
                            </h3>
                            <button
                                onClick={() => setShowMembersModal(false)}
                                className="text-slate-400 hover:text-white transition-colors"
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <div className="flex-1 overflow-y-auto p-4 space-y-3">
                            {membersLoading ? (
                                <div className="py-8 text-center text-slate-500 text-sm">
                                    Loading members...
                                </div>
                            ) : members.length === 0 ? (
                                <div className="py-8 text-center text-slate-500 text-sm">
                                    No other members found.
                                </div>
                            ) : (
                                members.map((member) => (
                                    <div
                                        key={member.user_id}
                                        className="flex items-center justify-between p-3 rounded-lg bg-slate-800/40 border border-slate-800"
                                    >
                                        <div className="min-w-0">
                                            <p className="text-sm font-medium text-white truncate">
                                                {member.full_name || "New User"}
                                            </p>
                                            <p className="text-xs text-slate-400 truncate">
                                                {member.email}
                                            </p>
                                            <span
                                                className={`text-[10px] px-1.5 py-0.5 rounded ${member.role === "ADMIN" ? "bg-accent-blue/20 text-accent-blue" : "bg-slate-700 text-slate-300"}`}
                                            >
                                                {member.role}
                                            </span>
                                        </div>
                                        {member.user_id !== user.sub && (
                                            <button
                                                onClick={() =>
                                                    handleRemoveMember(
                                                        member.user_id,
                                                    )
                                                }
                                                className="p-2 text-slate-500 hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors"
                                                title="Remove Member"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        )}
                                    </div>
                                ))
                            )}
                        </div>

                        <div className="p-4 border-t border-slate-800 bg-slate-900/50">
                            <p className="text-[10px] text-slate-500 text-center">
                                Admins can manage workspace access and
                                permissions.
                            </p>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
