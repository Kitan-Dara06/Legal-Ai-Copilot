"use client";

export const dynamic = 'force-dynamic';

import { useEffect, useState, useCallback, Suspense } from "react";
import { TopBar } from "@/components/layout/TopBar";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatThread } from "@/components/chat/ChatThread";
import { ChatInput } from "@/components/chat/ChatInput";
import { createClient } from "@/lib/supabase/client";
import {
  getMe,
  listFiles,
  deleteFile,
  createSession,
  uploadToSession,
  deleteSession,
  askQuestion,
  askAgent,
} from "@/lib/api";
import { type User, type FileItem, type SessionResponse, type ChatMessage } from "@/lib/types";
import { Scale } from "lucide-react";

export default function ChatPage() {
  const supabase = createClient();
  
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string>("");
  const [isInitializing, setIsInitializing] = useState(true);

  // Files
  const [files, setFiles] = useState<FileItem[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);

  // Session
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [isCreatingSession, setIsCreatingSession] = useState(false);
  const [isUploadingToSession, setIsUploadingToSession] = useState(false);

  // Chat
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isAnswering, setIsAnswering] = useState(false);
  const [useAgentic, setUseAgentic] = useState(false);

  // Initialize Auth
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session: authSession } }) => {
      if (authSession) {
        setToken(authSession.access_token);
        getMe(authSession.access_token)
          .then((u) => {
            setUser(u);
            setIsInitializing(false);
          })
          .catch(() => {
            window.location.href = "/login";
          });
      } else {
        window.location.href = "/login";
      }
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, authSession) => {
      if (authSession) setToken(authSession.access_token);
      else window.location.href = "/login";
    });

    return () => subscription.unsubscribe();
  }, []);

  // Fetch files when user is available
  const fetchFiles = useCallback(() => {
    if (!token || !user?.org_slug) return;
    setFilesLoading(true);
    listFiles(token, user.org_slug)
      .then((res) => setFiles(res.files || []))
      .catch(console.error)
      .finally(() => setFilesLoading(false));
  }, [token, user?.org_slug]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  const handleDeleteFile = async (id: number) => {
    if (!token || !user?.org_slug || !confirm("Delete this document?")) return;
    try {
      await deleteFile(token, id, user.org_slug);
      fetchFiles();
    } catch (e) {
      alert("Failed to delete file");
    }
  };

  const handleCreateSession = async (fileIds: number[]) => {
    if (!token || !user?.org_slug) return;
    setIsCreatingSession(true);
    try {
      const res = await createSession(token, fileIds, user.org_slug);
      setSession(res);
      setMessages([
        {
          role: "assistant",
          content: "I'm ready. I have fully indexed the selected contracts. What would you like to know?",
        },
      ]);
    } catch (e: any) {
      alert(e.message || "Failed to create session");
    } finally {
      setIsCreatingSession(false);
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
      let result;
      if (useAgentic) {
        result = await askAgent(token, session.session_id, content, user.org_slug);
      } else {
        result = await askQuestion(token, session.session_id, content, "fast", user.org_slug);
      }
      
      const aiMsg: ChatMessage = { role: "assistant", content: result.answer || "No answer provided." };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `**Error:** ${e.message || "Inference failed"}` },
      ]);
    } finally {
      setIsAnswering(false);
    }
  };


  if (isInitializing || !user) {
    return (
      <div className="min-h-screen bg-navy-950 flex flex-col items-center justify-center">
        <Scale className="w-12 h-12 text-accent-blue animate-pulse mb-6" strokeWidth={1.5} />
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
      />

      {/* Main Content Area */}
      <div className="flex-1 ml-[320px] flex flex-col relative">
        <TopBar 
          useAgentic={useAgentic}
          onToggleAgentic={() => setUseAgentic(!useAgentic)}
          sessionActive={!!session}
        />

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
