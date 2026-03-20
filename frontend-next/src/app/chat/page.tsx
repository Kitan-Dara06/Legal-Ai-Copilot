"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState, useCallback, Suspense } from "react";
import { TopBar } from "@/components/layout/TopBar";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatThread } from "@/components/chat/ChatThread";
import { ChatInput } from "@/components/chat/ChatInput";
import { createClient } from "@/lib/supabase/client";
import { Session as AuthSession } from "@supabase/supabase-js";
import {
    getMe,
    listFiles,
    getMembers,
    deleteFile,
    createSession,
    uploadToSession,
    deleteSession,
    askQuestion,
    askAgent,
    AppError,
} from "@/lib/api";
import {
    type User,
    type FileItem,
    type SessionResponse,
    type ChatMessage,
} from "@/lib/types";
import { Scale } from "lucide-react";

export default function ChatPage() {
    const supabase = createClient();

    const [user, setUser] = useState<User | null>(null);
    const [token, setToken] = useState<string>("");
    const [isInitializing, setIsInitializing] = useState(true);

    // Files
    const [files, setFiles] = useState<FileItem[]>([]);
    const [filesLoading, setFilesLoading] = useState(true);

    // Session
    const [session, setSession] = useState<SessionResponse | null>(null);
    const [isCreatingSession, setIsCreatingSession] = useState(false);
    const [isUploadingToSession, setIsUploadingToSession] = useState(false);

    // Chat
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [isAnswering, setIsAnswering] = useState(false);

    // Initialize Auth
    useEffect(() => {
        let mounted = true;
        console.log("[ChatPage] Initializing auth...");

        supabase.auth
            .getSession()
            .then(
                ({
                    data: { session: authSession },
                }: {
                    data: { session: AuthSession | null };
                }) => {
                    if (!mounted) return;
                    console.log("[ChatPage] Session check:", !!authSession);

                    if (authSession) {
                        console.log(
                            "[ChatPage] Token obtained, calling getMe...",
                        );
                        setToken(authSession.access_token);

                        // Force a refresh of the user data to ensure we are in the correct org
                        getMe(authSession.access_token)
                            .then((u) => {
                                console.log(
                                    "[ChatPage] getMe success:",
                                    u?.email,
                                    u?.org_slug,
                                );
                                if (mounted) {
                                    setUser(u);
                                    setIsInitializing(false);
                                }
                            })
                            .catch((err: any) => {
                                console.error("[ChatPage] getMe failed:", err);
                                // If backend says setup is required (no local account yet),
                                // redirect to /setup so the user can create their org.
                                if (
                                    err instanceof AppError &&
                                    (err.code === "setup_required" || err.status === 403)
                                ) {
                                    window.location.href = "/setup";
                                } else {
                                    if (mounted) setIsInitializing(false);
                                }
                            });
                    } else {
                        if (mounted) setIsInitializing(false);
                    }
                },
            );

        const {
            data: { subscription },
        } = supabase.auth.onAuthStateChange(
            async (event: any, authSession: AuthSession | null) => {
                if (!mounted) return;
                console.log(
                    "[ChatPage] Auth state change:",
                    event,
                    !!authSession,
                );

                if (event === "SIGNED_OUT") {
                    setUser(null);
                    setToken("");
                    window.location.href = "/login";
                    return;
                }

                if (authSession) {
                    setToken(authSession.access_token);
                    // If we just signed in or the session refreshed, verify identity
                    if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") {
                        try {
                            const u = await getMe(authSession.access_token);
                            setUser(u);
                        } catch (e) {
                            console.error(
                                "[ChatPage] Auth change getMe failed:",
                                e,
                            );
                        }
                    }
                } else if (event === "INITIAL_SESSION" && !authSession) {
                    // If Supabase finishes checking and no session exists, stop loading
                    if (mounted) setIsInitializing(false);
                }
            },
        );

        // Safety timeout: If nothing happens for 8 seconds, stop the spinner
        const timeout = setTimeout(() => {
            if (mounted && isInitializing) {
                console.warn("[ChatPage] Initialization timed out");
                setIsInitializing(false);
            }
        }, 8000);

        return () => {
            clearTimeout(timeout);
            mounted = false;
            subscription.unsubscribe();
        };
    }, []);

    const fetchFiles = useCallback(
        (isSilent = false) => {
            if (!token || !user?.org_slug) return;
            if (!isSilent) setFilesLoading(true);
            listFiles(token, user.org_slug)
                .then((res) => {
                    // Update the list silently to avoid UI flickering
                    setFiles(res.files || []);
                })
                .catch(console.error)
                .finally(() => {
                    if (!isSilent) setFilesLoading(false);
                });
        },
        [token, user?.org_slug],
    );

    const handleSwitchOrg = async (orgSlug: string) => {
        if (!token) return;
        try {
            const newUser = await getMe(token, orgSlug);
            setUser(newUser);
            setSession(null);
            setMessages([]);
        } catch (err) {
            console.error("Error switching org:", err);
            alert("Failed to switch workspace.");
        }
    };

    useEffect(() => {
        // Initial load (shows spinner)
        fetchFiles(false);

        // Auto-refresh file list every 10 seconds silently in the background
        const interval = setInterval(() => fetchFiles(true), 10000);
        return () => clearInterval(interval);
    }, [fetchFiles]);

    const handleDeleteFile = async (id: number) => {
        if (!token || !user?.org_slug || !confirm("Delete this document?"))
            return;
        try {
            await deleteFile(token, id, user.org_slug);
            fetchFiles();
        } catch (e) {
            alert("Failed to delete file");
        }
    };

    const handleCreateSession = async (fileIds: number[]) => {
        console.log("[ChatPage] handleCreateSession triggered", {
            fileIds,
            hasToken: !!token,
            orgSlug: user?.org_slug,
        });
        if (!token || !user?.org_slug) {
            console.warn(
                "[ChatPage] Missing token or org_slug, aborting session creation",
            );
            return;
        }
        setIsCreatingSession(true);
        try {
            console.log("[ChatPage] Calling createSession API...");
            const res = await createSession(token, fileIds, user.org_slug);
            console.log("[ChatPage] createSession API response received:", res);

            if (!res || !res.session_id) {
                console.error(
                    "[ChatPage] Session creation returned invalid data structure:",
                    res,
                );
                throw new Error(
                    "The server failed to initialize a valid workspace session.",
                );
            }

            console.log(
                "[ChatPage] Updating state with new session:",
                res.session_id,
            );

            // Critical: Update state
            setSession(res);

            console.log("[ChatPage] Setting initial messages...");
            const initialMessage: ChatMessage = {
                role: "assistant",
                content:
                    "I'm ready. I have fully indexed the selected contracts. What would you like to know?",
            };
            setMessages([initialMessage]);
            console.log("[ChatPage] Session initialization complete.");
        } catch (e: any) {
            console.error(
                "[ChatPage] CRITICAL: Session creation error caught:",
                e,
            );
            alert(
                e.message ||
                    "Failed to create session. Please check if the documents are still processing.",
            );
        } finally {
            setIsCreatingSession(false);
            console.log("[ChatPage] handleCreateSession finished (finally)");
        }
    };

    const handleTerminateSession = async () => {
        if (!token || !session?.session_id || !user?.org_slug) return;
        try {
            await deleteSession(token, session.session_id, user.org_slug);
        } catch (e) {
            console.error("Failed to cleanly terminate session", e);
        }
        setSession(null);
        setMessages([]);
    };

    // Upload multiple file IDs at once (simplified since we have IDs)
    // The Streamlit app was uploading physical files to the session endpoint.
    // We'll mimic that by recreating the session or using a backend endpoint if it accepts IDs.
    // Actually, the backend `POST /session/{id}/upload` takes a physical file.
    // Let's implement that properly later - for now, we just tell the user to recreate.
    const handleUploadToSession = async (fileIds: number[]) => {
        alert("Recreating session with new files...");
        await handleCreateSession(fileIds);
    };

    const handleSendMessage = async (content: string) => {
        if (!token || !session?.session_id || !user?.org_slug) return;

        // Add user message to UI
        const userMsg: ChatMessage = { role: "user", content };
        setMessages((prev) => [...prev, userMsg]);
        setIsAnswering(true);

        try {
            const result = await askAgent(
                token,
                session.session_id,
                content,
                user.org_slug,
            );

            const aiMsg: ChatMessage = {
                role: "assistant",
                content: result.answer || "No answer provided.",
            };
            setMessages((prev) => [...prev, aiMsg]);
        } catch (e: any) {
            setMessages((prev) => [
                ...prev,
                {
                    role: "assistant",
                    content: `**Error:** ${e.message || "Inference failed"}`,
                },
            ]);
        } finally {
            setIsAnswering(false);
        }
    };

    if (isInitializing || !user) {
        return (
            <div className="min-h-screen bg-navy-950 flex flex-col items-center justify-center">
                <Scale
                    className="w-12 h-12 text-accent-blue animate-pulse mb-6"
                    strokeWidth={1.5}
                />
                <div className="w-48 h-1 overflow-hidden bg-slate-800 rounded-full">
                    <div className="h-full bg-accent-blue animate-[ping_1.5s_cubic-bezier(0,0,0.2,1)_infinite]" />
                </div>
            </div>
        );
    }

    return (
        <div className="flex h-screen bg-navy-950 overflow-hidden">
            {/* Sidebar - fixed 320px width */}
            <Sidebar
                user={user}
                token={token}
                files={files}
                filesLoading={filesLoading}
                onUploadSuccess={fetchFiles}
                onDeleteFile={handleDeleteFile}
                session={session}
                onCreateSession={handleCreateSession}
                onTerminate={handleTerminateSession}
                onUploadToSession={handleUploadToSession}
                isCreatingSession={isCreatingSession}
                isUploadingToSession={isUploadingToSession}
                onSwitchOrg={handleSwitchOrg}
            />

            {/* Main Content Area */}
            <div className="flex-1 ml-[320px] flex flex-col relative">
                <TopBar sessionActive={!!session} />

                {/* Chat Thread Area (starts below top bar (64px) and above input (96px max)) */}
                <div className="flex-1 mt-16 relative">
                    <ChatThread messages={messages} isLoading={isAnswering} />

                    {/* Floating Input aligned to bottom */}
                    <div className="absolute bottom-0 left-0 right-0 p-6 bg-gradient-to-t from-navy-950 via-navy-950/80 to-transparent pointer-events-none">
                        <div className="pointer-events-auto">
                            <ChatInput
                                onSend={handleSendMessage}
                                isLoading={isAnswering}
                            />
                        </div>
                        {!session && (
                            <div className="absolute inset-0 bg-navy-950/40 backdrop-blur-[2px] z-10 flex items-center justify-center pointer-events-auto mt-20">
                                <div className="bg-slate-900 border border-slate-700 text-slate-300 text-sm px-4 py-2 rounded-full shadow-lg">
                                    Initialize a workspace to begin chatting
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
