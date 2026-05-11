"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getGoalStatus } from "@/lib/api";
import type { GoalDetail } from "@/lib/types";

interface GoalStatusState {
    goal: GoalDetail | null;
    loading: boolean;
    error: string | null;
}

export function usePollGoalStatus(
    token: string | null,
    workspaceId: string,
    goalId: string | null,
    orgSlug?: string,
): GoalStatusState & { stop: () => void } {
    const [state, setState] = useState<GoalStatusState>({
        goal: null,
        loading: !!goalId,
        error: null,
    });
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const stop = useCallback(() => {
        if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
        }
    }, []);

    useEffect(() => {
        if (!token || !goalId) {
            setState({ goal: null, loading: false, error: null });
            return;
        }

        setState(prev => ({ ...prev, loading: true }));

        const poll = async () => {
            try {
                const data = await getGoalStatus(token, workspaceId, goalId, orgSlug);
                setState({
                    goal: data,
                    loading: false,
                    error: null,
                });

                if (data.status === "COMPLETED" || data.status === "FAILED") {
                    stop();
                }
            } catch (err) {
                setState(prev => ({
                    ...prev,
                    loading: false,
                    error: err instanceof Error ? err.message : "Polling failed",
                }));
            }
        };

        poll();
        intervalRef.current = setInterval(poll, 2000);

        return () => stop();
    }, [token, workspaceId, goalId, orgSlug, stop]);

    return { ...state, stop };
}
