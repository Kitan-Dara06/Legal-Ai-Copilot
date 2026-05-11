"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getDocumentStatus } from "@/lib/api";

interface DocumentStatusState {
  status: string;
  stages?: Record<string, boolean> | null;
  error?: string | null;
  loading: boolean;
}

const INITIAL_STATE: DocumentStatusState = {
  status: "PENDING",
  stages: null,
  error: null,
  loading: true,
};

export function usePollDocumentStatus(
  token: string | null,
  workspaceId: string,
  documentId: string | null,
  orgSlug?: string,
): DocumentStatusState & { stop: () => void } {
  const [state, setState] = useState<DocumentStatusState>(INITIAL_STATE);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!token || !documentId) {
      setState(INITIAL_STATE);
      return;
    }

    const poll = async () => {
      try {
        const data = await getDocumentStatus(
          token,
          workspaceId,
          documentId,
          orgSlug,
        );
        setState({
          status: data.status,
          stages: data.stages || null,
          error: data.error || null,
          loading: false,
        });

        if (data.status === "READY" || data.status === "FAILED") {
          stop();
        }
      } catch {
        // Silently retry on network errors
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => stop();
  }, [token, documentId, orgSlug, stop]);

  return { ...state, stop };
}
