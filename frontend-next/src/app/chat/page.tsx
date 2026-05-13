"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState, useCallback, useRef } from "react";
import { TopBar } from "@/components/layout/TopBar";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatThread } from "@/components/chat/ChatThread";
import { ChatInput } from "@/components/chat/ChatInput";
import { AmbiguityGate } from "@/components/chat/AmbiguityGate";
import { ActionQueue } from "@/components/goals/ActionQueue";
import { ReasonResult } from "@/components/chat/ReasonResult";
import { createClient } from "@/lib/supabase/client";
import {
  getMe,
  listWorkspaces,
  getWorkspace,
  createWorkspace,
  createGoal,
  getGoalStatus,
  getGoalResult,
  confirmGoalIntent,
  listGoals,
  createWorkspaceScopedSession,
  deleteDocument,
  AppError,
} from "@/lib/api";
import {
  type User,
  type FileItem,
  type GoalSummary,
  type GoalIntent,
  type WorkspaceDocument,
  type ChatMessage,
} from "@/lib/types";
import { Scale } from "lucide-react";

interface GoalMessage {
  id: string;
  goal_text: string;
  status: string;
  intent?: GoalIntent | null;
  answer?: string | null;
  actions?: {
    id: string;
    action_type: string;
    description: string;
    status: string;
    urgency: number;
  }[];
  logs?: {
    id: string;
    tool_name: string;
    status: string;
    summary: string;
    created_at: string;
  }[];
  findings?: {
    id: string;
    claim: string;
    confidence: number;
    supporting_citations?: any;
    reference_chain?: any;
    definitional_conflicts?: any;
    escalated: boolean;
    escalation_type?: string | null;
  }[];
  created_at: string;
  // Ambiguity gate state
  needsIntentConfirmation?: boolean;
  suggestedIntent?: GoalIntent;
  confidence?: number;
}

export default function ChatPage() {
  const supabase = createClient();

  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string>("");
  const [isInitializing, setIsInitializing] = useState(true);

  // Workspace
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [workspaceName, setWorkspaceName] = useState<string>("");

  // Files (from workspace)
  const [files, setFiles] = useState<FileItem[]>([]);
  const [filesLoading, setFilesLoading] = useState(true);

  // Scoped document session (optional — limits search to selected documents)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);

  // Goals as chat history
  const [goals, setGoals] = useState<GoalMessage[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const pollingRef = useRef<Map<string, NodeJS.Timeout>>(new Map());

  // Sidebar
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  // ─── Auth Initialization ─────────────────────────────────────────────────

  useEffect(() => {
    let mounted = true;
    console.log("[ChatPage] Initializing auth...");

    supabase.auth
      .getSession()
      .then(
        ({
          data: { session: authSession },
        }: {
          data: { session: import("@supabase/supabase-js").Session | null };
        }) => {
          if (!mounted) return;
          if (authSession) {
            setToken(authSession.access_token);
            const savedOrg =
              localStorage.getItem("legalrag_active_org") || undefined;
            getMe(authSession.access_token, savedOrg)
              .then((u) => {
                if (mounted) {
                  setUser(u);
                  setIsInitializing(false);
                }
              })
              .catch((err: unknown) => {
                if (
                  err instanceof AppError &&
                  (err.code === "setup_required" || err.status === 403)
                ) {
                  mounted = false;
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
      async (
        event: string,
        authSession: import("@supabase/supabase-js").Session | null,
      ) => {
        if (!mounted) return;
        if (event === "SIGNED_OUT") {
          setUser(null);
          setToken("");
          localStorage.removeItem("legalrag_active_org");
          window.location.href = "/login";
          return;
        }
        if (
          authSession &&
          (event === "SIGNED_IN" || event === "TOKEN_REFRESHED")
        ) {
          setToken(authSession.access_token);
          try {
            const savedOrg =
              localStorage.getItem("legalrag_active_org") || undefined;
            const u = await getMe(authSession.access_token, savedOrg);
            setUser(u);
          } catch (e) {
            console.error("[ChatPage] Auth change getMe failed:", e);
          }
        }
      },
    );

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

  // ─── Workspace Initialization ────────────────────────────────────────────

  useEffect(() => {
    if (!token || !user?.org_slug || workspaceId) return;

    listWorkspaces(token, user.org_slug)
      .then((workspaces) => {
        if (workspaces && workspaces.length > 0) {
          const ws = workspaces[0];
          setWorkspaceId(ws.workspace_id);
          setWorkspaceName(ws.name);
        } else if (token && user?.org_slug) {
          // No workspaces exist — create a default one
          console.warn("[ChatPage] No workspaces found, creating default...");
          createWorkspace(token, "Default Workspace", undefined, user.org_slug)
            .then((ws) => {
              setWorkspaceId(ws.workspace_id);
              setWorkspaceName(ws.name);
            })
            .catch((err) =>
              console.error(
                "[ChatPage] Failed to create default workspace:",
                err,
              ),
            );
        }
      })
      .catch(console.error);
  }, [token, user?.org_slug, workspaceId]);

  // ─── Load Workspace Docs + Goals ─────────────────────────────────────────

  const orgSlug = user?.org_slug;

  const loadWorkspaceData = useCallback(() => {
    if (!token || !orgSlug || !workspaceId) return;

    getWorkspace(token, workspaceId, orgSlug)
      .then((ws) => {
        const docs = (ws.documents || []).map((d: WorkspaceDocument) => ({
          file_id:
            parseInt(d.document_id.replace(/-/g, "").substring(0, 8), 16) || 0,
          document_id: d.document_id,
          filename: d.filename,
          upload_date: d.upload_date,
          status: d.status as "PENDING" | "PROCESSING" | "READY" | "FAILED",
          stages: d.stages,
        }));
        setFiles(docs);
      })
      .catch(console.error)
      .finally(() => setFilesLoading(false));

    listGoals(token, workspaceId, orgSlug)
      .then((goalList) => {
        const msgs: GoalMessage[] = ((goalList && goalList.goals) || []).map(
          (g: GoalSummary) => ({
            id: g.id,
            goal_text: g.goal_text,
            status: g.status,
            intent: g.intent,
            answer: g.answer || undefined,
            created_at: g.created_at,
          }),
        );
        setGoals(msgs);
      })
      .catch(console.error);
  }, [token, orgSlug, workspaceId]);

  useEffect(() => {
    loadWorkspaceData();
  }, [loadWorkspaceData]);

  // ─── Goal Polling ────────────────────────────────────────────────────────

  const startPolling = useCallback(
    (goalId: string) => {
      if (!token || !orgSlug || !workspaceId) return;
      if (pollingRef.current.has(goalId)) return;

      const interval = setInterval(async () => {
        try {
          const detail = await getGoalStatus(
            token,
            workspaceId,
            goalId,
            orgSlug,
          );

          setGoals((prev) =>
            prev.map((g) =>
              g.id === goalId
                ? { ...g, status: detail.status, intent: detail.intent }
                : g,
            ),
          );

          if (detail.status === "COMPLETED") {
            clearInterval(interval);
            pollingRef.current.delete(goalId);
            setIsProcessing(false);

            // Fetch full result
            const result = await getGoalResult(
              token,
              workspaceId,
              goalId,
              orgSlug,
            );
            setGoals((prev) =>
              prev.map((g) =>
                g.id === goalId
                  ? {
                      ...g,
                      status: "COMPLETED",
                      answer: result.answer,
                      intent: (result.intent as GoalIntent) || g.intent,
                      actions: result.actions,
                      logs: result.logs,
                      findings: result.findings,
                    }
                  : g,
              ),
            );
          } else if (detail.status === "FAILED") {
            clearInterval(interval);
            pollingRef.current.delete(goalId);
            setIsProcessing(false);
            setGoals((prev) =>
              prev.map((g) =>
                g.id === goalId
                  ? { ...g, status: "FAILED", answer: "Processing failed." }
                  : g,
              ),
            );
          }
        } catch (err) {
          console.error(`[ChatPage] Polling error for goal ${goalId}:`, err);
          clearInterval(interval);
          pollingRef.current.delete(goalId);
          setIsProcessing(false);
        }
      }, 2000);

      pollingRef.current.set(goalId, interval);
    },
    [token, orgSlug, workspaceId],
  );

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      pollingRef.current.forEach((interval) => clearInterval(interval));
      pollingRef.current.clear();
    };
  }, []);

  // ─── Send Message (create goal) ──────────────────────────────────────────

  const handleSendMessage = async (content: string) => {
    if (!token || !user?.org_slug || !workspaceId || isProcessing) return;

    setIsProcessing(true);

    // Add user message placeholder — will be updated by polling
    const tempGoal: GoalMessage = {
      id: `temp-${Date.now()}`,
      goal_text: content,
      status: "PROCESSING",
      created_at: new Date().toISOString(),
    };
    setGoals((prev) => [...prev, tempGoal]);

    try {
      const response = await createGoal(
        token,
        workspaceId,
        content,
        activeSessionId || undefined,
        user.org_slug,
      );

      // Replace temp with real goal
      setGoals((prev) =>
        prev.map((g) =>
          g.id === tempGoal.id
            ? {
                ...g,
                id: response.goal_id,
                status: response.status,
                intent: (response.primary_intent as GoalIntent) || null,
                confidence: response.intent_confidence,
                needsIntentConfirmation:
                  response.status === "PROCESSING" &&
                  !response.workflow_id &&
                  !response.answer &&
                  response.intent_confidence !== undefined &&
                  response.intent_confidence < 0.8,
                suggestedIntent:
                  (response.primary_intent as GoalIntent) || undefined,
              }
            : g,
        ),
      );

      // If we got an answer immediately (ANALYZE path), show it
      if (response.answer) {
        setGoals((prev) =>
          prev.map((g) =>
            g.id === response.goal_id
              ? { ...g, status: "COMPLETED", answer: response.answer }
              : g,
          ),
        );
        setIsProcessing(false);
        return;
      }

      // Otherwise start polling
      startPolling(response.goal_id);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to process goal.";
      console.error("[ChatPage] createGoal failed:", err);
      setGoals((prev) =>
        prev.map((g) =>
          g.id === tempGoal.id
            ? {
                ...g,
                status: "FAILED",
                answer: message,
              }
            : g,
        ),
      );
      setIsProcessing(false);
    }
  };

  // ─── Ambiguity Gate ──────────────────────────────────────────────────────

  const handleConfirmIntent = async (goalId: string, intent: GoalIntent) => {
    if (!token || !user?.org_slug || !workspaceId) return;

    try {
      await confirmGoalIntent(
        token,
        workspaceId,
        goalId,
        intent,
        user.org_slug,
      );
      setGoals((prev) =>
        prev.map((g) =>
          g.id === goalId
            ? { ...g, needsIntentConfirmation: false, intent }
            : g,
        ),
      );
      startPolling(goalId);
    } catch (err) {
      console.error("[ChatPage] confirmIntent failed:", err);
    }
  };

  // ─── Document / Scope Management ─────────────────────────────────────────

  const handleToggleDocumentSelection = (docId: string) => {
    setSelectedDocumentIds((prev) =>
      prev.includes(docId)
        ? prev.filter((id) => id !== docId)
        : [...prev, docId],
    );

    // If any are selected, create a scoped session
    const newSelection = selectedDocumentIds.includes(docId)
      ? selectedDocumentIds.filter((id) => id !== docId)
      : [...selectedDocumentIds, docId];

    if (newSelection.length === 0) {
      setActiveSessionId(null);
    } else if (token && workspaceId && user?.org_slug) {
      createWorkspaceScopedSession(
        token,
        workspaceId,
        newSelection,
        user.org_slug,
      )
        .then((res) => setActiveSessionId(res.session_id))
        .catch(console.error);
    }
  };

  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);

  const handleDeleteFile = async (id: number) => {
    if (!token || !user?.org_slug || !workspaceId) return;
    setDeleteConfirmId(id);
  };

  const confirmDelete = async () => {
    if (deleteConfirmId === null || !workspaceId || !user?.org_slug) return;
    const file = files.find((f) => f.file_id === deleteConfirmId);
    if (file?.document_id) {
      try {
        await deleteDocument(
          token,
          workspaceId,
          file.document_id,
          user.org_slug,
        );
        loadWorkspaceData();
      } catch (e) {
        console.error("Delete failed:", e);
        alert("Failed to delete document.");
      }
    }
    setDeleteConfirmId(null);
  };

  const handleUploadSuccess = () => {
    loadWorkspaceData();
  };

  const handleSwitchOrg = async (orgSlug: string) => {
    if (!token) return;
    try {
      const newUser = await getMe(token, orgSlug);
      setUser(newUser);
      setWorkspaceId(null);
      setWorkspaceName("");
      setGoals([]);
      setActiveSessionId(null);
      setSelectedDocumentIds([]);
      localStorage.setItem("legalrag_active_org", orgSlug);
    } catch (err) {
      console.error("Error switching org:", err);
      alert("Failed to switch workspace.");
    }
  };

  // ─── Render Helpers ──────────────────────────────────────────────────────

  const goalsToChatMessages = (): ChatMessage[] => {
    const msgs: ChatMessage[] = [];

    for (const goal of goals) {
      // User message
      msgs.push({ role: "user", content: goal.goal_text });

      // Skip system/ambiguity messages — they render via children
      if (goal.needsIntentConfirmation && goal.suggestedIntent) {
        continue;
      }

      // Processing
      if (goal.status === "PROCESSING") {
        msgs.push({ role: "assistant", content: "⏳ Processing..." });
        continue;
      }

      // Failed
      if (goal.status === "FAILED") {
        msgs.push({
          role: "assistant",
          content: `❌ ${goal.answer || "Processing failed."}`,
        });
        continue;
      }

      // Completed: ACT with actions rendering via ActionQueue child
      // Completed: REASON with findings rendered via ReasonResult child
      if (goal.status === "COMPLETED") {
        if (goal.intent === "ACT" && goal.actions && goal.actions.length > 0) {
          msgs.push({
            role: "assistant",
            content: goal.answer || "Draft ready for review.",
          });
        } else if (goal.intent === "REASON") {
          msgs.push({
            role: "assistant",
            content: goal.answer || "Reasoning complete.",
          });
        } else {
          msgs.push({
            role: "assistant",
            content: goal.answer || "Completed.",
          });
        }
      }
    }

    return msgs;
  };

  // ─── Render ──────────────────────────────────────────────────────────────

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

  const chatMessages = goalsToChatMessages();

  return (
    <div className="flex h-screen bg-navy-950 overflow-hidden relative">
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div
        className={`
          fixed inset-y-0 left-0 z-40 transform transition-all duration-300 ease-in-out
          ${isSidebarOpen ? "translate-x-0" : "-translate-x-full"}
          md:relative md:translate-x-0 md:flex-shrink-0 overflow-hidden
          ${isSidebarOpen ? "md:w-[320px]" : "md:w-0"}
        `}
      >
        <Sidebar
          user={user}
          token={token}
          workspaceId={workspaceId || undefined}
          files={files}
          filesLoading={filesLoading}
          onUploadSuccess={handleUploadSuccess}
          onDeleteFile={handleDeleteFile}
          selectedDocumentIds={selectedDocumentIds}
          onToggleDocumentSelection={handleToggleDocumentSelection}
          activeSessionId={activeSessionId}
          onSwitchOrg={handleSwitchOrg}
          workspaceName={workspaceName}
        />
      </div>

      <div className="flex-1 flex flex-col relative w-full md:w-auto min-w-0 md:ml-0">
        <TopBar
          sessionActive={!!activeSessionId}
          onMenuClick={() => setIsSidebarOpen(true)}
        />

        <div className="flex-1 mt-16 relative overflow-hidden flex flex-col">
          <ChatThread messages={chatMessages} isLoading={isProcessing} />

          {/* Special UI components rendered after the chat thread */}
          {goals.map((goal) => {
            if (goal.needsIntentConfirmation && goal.suggestedIntent) {
              return (
                <AmbiguityGate
                  key={`ambiguity-${goal.id}`}
                  goalText={goal.goal_text}
                  goalId={goal.id}
                  onConfirm={(intent) => handleConfirmIntent(goal.id, intent)}
                />
              );
            }
            if (
              goal.intent === "ACT" &&
              goal.actions &&
              goal.actions.length > 0
            ) {
              return (
                <ActionQueue
                  key={`actions-${goal.id}`}
                  actions={goal.actions}
                  draft={goal.answer}
                />
              );
            }
            if (goal.intent === "REASON") {
              return (
                <ReasonResult
                  key={`reason-${goal.id}`}
                  answer={goal.answer}
                  findings={goal.findings}
                  logs={goal.logs}
                />
              );
            }
            return null;
          })}

          <div className="absolute bottom-0 left-0 right-0 p-6 bg-gradient-to-t from-navy-950 via-navy-950/80 to-transparent pointer-events-none">
            <div className="pointer-events-auto">
              <ChatInput onSend={handleSendMessage} isLoading={isProcessing} />
            </div>
            {!workspaceId && (
              <div className="absolute inset-0 bg-navy-950/40 backdrop-blur-[2px] z-10 flex items-center justify-center pointer-events-auto mt-20">
                <div className="bg-slate-900 border border-slate-700 text-slate-300 text-sm px-4 py-2 rounded-full shadow-lg">
                  Select a workspace to begin
                </div>
              </div>
            )}
            {activeSessionId && selectedDocumentIds.length > 0 && (
              <div className="mt-2 text-xs text-slate-500 text-center">
                🔍 Searching {selectedDocumentIds.length} selected document(s)
                <button
                  className="ml-2 text-accent-blue hover:underline"
                  onClick={() => {
                    setActiveSessionId(null);
                    setSelectedDocumentIds([]);
                  }}
                >
                  Clear scope
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {deleteConfirmId !== null && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center backdrop-blur-sm">
          <div className="bg-slate-900 border border-slate-700 rounded-lg p-6 max-w-sm w-full mx-4 shadow-xl">
            <h3 className="text-white font-semibold mb-2">Delete Document</h3>
            <p className="text-slate-400 text-sm mb-6">
              Are you sure you want to delete this document? This action cannot
              be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setDeleteConfirmId(null)}
                className="px-4 py-2 text-sm text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                className="px-4 py-2 text-sm text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
