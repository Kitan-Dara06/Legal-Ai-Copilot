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
    getSession,
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
    const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);

    // Chat
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [isAnswering, setIsAnswering] = useState(false);
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);

    // Initialize Local Storage State for messages and selections
    useEffect(() => {
        try {
            const savedMessages = localStorage.getItem("legalrag_active_messages");
            const savedSelections = localStorage.getItem("legalrag_selected_files");
            
            if (savedMessages) setMessages(JSON.parse(savedMessages));
            if (savedSelections) setSelectedFileIds(JSON.parse(savedSelections));
        } catch (e) {
            console.error("Failed to restore state from local storage", e);
        }
    }, []);

    // Hydrate Session from API once user/token are ready
    useEffect(() => {
        if (!token || !user?.org_slug) return;
        
        try {
            const savedSessionStr = localStorage.getItem("legalrag_active_session");
            if (savedSessionStr) {
                const savedSession = JSON.parse(savedSessionStr);
                if (savedSession?.session_id) {
                    // Fetch full session details so we get the .files array
                    getSession(token, savedSession.session_id, user.org_slug)
                        .then(fullSession => {
                            setSession(fullSession);
                            // Verify selections match the hydrated session if they were lost
                            if (fullSession.files && fullSession.files.length > 0 && selectedFileIds.length === 0) {
                                setSelectedFileIds(fullSession.files.map(f => f.file_id));
                            }
                        })
                        .catch(err => {
                            console.error("Failed to restore session details:", err);
                            
                            // Only clear local storage if the session explicitly doesn't exist anymore (404)
                            // or if the error indicates an invalid workspace.
                            if (err instanceof AppError && (err.status === 404 || err.status === 400)) {
                                console.warn("Session expired or invalid, cleaning up state...");
                                setSession(null);
                                localStorage.removeItem("legalrag_active_session");
                            }
                            // Otherwise, we keep the session_id in localStorage so it can be retried 
                            // later or after the backend recover.
                        });
                }
            }
        } catch (e) {
            console.error(e);
        }
    }, [token, user?.org_slug]);

    // Sync to Local Storage
    useEffect(() => {
        if (session) {
            localStorage.setItem("legalrag_active_session", JSON.stringify(session));
        } else {
            localStorage.removeItem("legalrag_active_session");
        }
    }, [session]);

    useEffect(() => {
        if (messages.length > 0) {
            localStorage.setItem("legalrag_active_messages", JSON.stringify(messages));
        } else {
            localStorage.removeItem("legalrag_active_messages");
        }
    }, [messages]);

    useEffect(() => {
        localStorage.setItem("legalrag_selected_files", JSON.stringify(selectedFileIds));
    }, [selectedFileIds]);

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
                        const savedOrg = localStorage.getItem("legalrag_active_org") || undefined;
                        getMe(authSession.access_token, savedOrg)
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
                    localStorage.removeItem("legalrag_active_session");
                    localStorage.removeItem("legalrag_active_messages");
                    localStorage.removeItem("legalrag_active_org");
                    window.location.href = "/login";
                    return;
                }

                if (authSession) {
                    setToken(authSession.access_token);
                    // If we just signed in or the session refreshed, verify identity
                    if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") {
                        try {
                            const savedOrg = localStorage.getItem("legalrag_active_org") || undefined;
                            const u = await getMe(authSession.access_token, savedOrg);
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
            localStorage.setItem("legalrag_active_org", orgSlug);
            localStorage.removeItem("legalrag_active_session");
            localStorage.removeItem("legalrag_active_messages");
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
                "[ChatPage] Fetching full session details for:",
                res.session_id,
            );
            const fullSession = await getSession(
                token,
                res.session_id,
                user.org_slug,
            );

            // Critical: Update state with the FULL session (includes .files)
            setSession(fullSession);

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
        if (!token || !user?.org_slug || !session) return;
        try {
            await deleteSession(token, session.session_id, user.org_slug);
            setSession(null);
            setMessages([]);
            setSelectedFileIds([]);
            localStorage.removeItem("legalrag_active_session");
            localStorage.removeItem("legalrag_active_messages");
            localStorage.removeItem("legalrag_selected_files");
        } catch (e) {
            alert("Failed to terminate session");
        }
    };

    const handleToggleSelection = (id: number) => {
        setSelectedFileIds((prev) =>
            prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
        );
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
        <div className="flex h-screen bg-navy-950 overflow-hidden relative">
            {/* Mobile Sidebar Overlay */}
            {isSidebarOpen && (
                <div 
                    className="fixed inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm transition-opacity"
                    onClick={() => setIsSidebarOpen(false)}
                />
            )}

            {/* Sidebar Container */}
            <div className={`
                fixed inset-y-0 left-0 z-40 transform transition-all duration-300 ease-in-out
                ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}
                md:relative md:translate-x-0 md:flex-shrink-0 overflow-hidden
                ${isSidebarOpen ? 'md:w-[320px]' : 'md:w-0'}
            `}>
                <Sidebar
                    user={user}
                    token={token}
                    files={files}
                    filesLoading={filesLoading}
                    onUploadSuccess={fetchFiles}
                    onDeleteFile={handleDeleteFile}
                    session={session}
                    selectedFileIds={selectedFileIds}
                    onToggleSelection={handleToggleSelection}
                    onCreateSession={handleCreateSession}
                    onTerminate={handleTerminateSession}
                    onUploadToSession={handleUploadToSession}
                    isCreatingSession={isCreatingSession}
                    isUploadingToSession={isUploadingToSession}
                    onSwitchOrg={handleSwitchOrg}
                />
            </div>

            {/* Main Content Area */}
            <div className="flex-1 flex flex-col relative w-full md:w-auto min-w-0 md:ml-0">
                <TopBar sessionActive={!!session} onMenuClick={() => setIsSidebarOpen(true)} />

                {/* Chat Thread Area (starts below top bar (64px) and above input (96px max)) */}
                <div className="flex-1 mt-16 relative overflow-hidden flex flex-col">
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
