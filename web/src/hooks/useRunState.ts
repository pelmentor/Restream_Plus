import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { RunStateView, type RunStateT, type RunStateViewT } from "@/lib/schemas/run";

export const RUN_STATE_QUERY_KEY = ["run", "state"] as const;

export interface UseRunStateResult {
  runState: RunStateT;
  heartbeatAgeSeconds: number;
  droppedTotal: number;
  runStartedAt: Date | null;
  isPending: boolean;
}

/**
 * Reads the run-state cache. The cache is seeded by a one-shot REST
 * call (`GET /api/run/state`) and continually updated by the WS hook
 * inside `StatusStreamHost`. `staleTime: Infinity` because the WS owns
 * freshness; this query is a cold-boot seed only.
 *
 * `runStartedAt` is derived from `run_state_changed_at` when (and only
 * when) the supervisor is in the `live` state. Survives reconnect
 * because the backend `state.full` carries the field (phase-8-design-
 * memo §A).
 */
export function useRunState(): UseRunStateResult {
  const { data, isPending } = useQuery<RunStateViewT, ApiError>({
    queryKey: RUN_STATE_QUERY_KEY,
    queryFn: () => apiFetch("run/state", {}, RunStateView),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });

  return useMemo(() => {
    const runState: RunStateT = data?.run_state ?? "offline";
    const runStartedAt =
      runState === "live" && data?.run_state_changed_at !== null && data?.run_state_changed_at !== undefined
        ? new Date(data.run_state_changed_at)
        : null;
    return {
      runState,
      heartbeatAgeSeconds: data?.heartbeat_age_seconds ?? 0,
      droppedTotal: data?.dropped_total ?? 0,
      runStartedAt,
      isPending,
    };
  }, [data, isPending]);
}
