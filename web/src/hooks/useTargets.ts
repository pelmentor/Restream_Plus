import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { TargetList, type TargetT } from "@/lib/schemas/targets";
import { RunStateView, type RunStateViewT, type TargetSnapshotT } from "@/lib/schemas/run";

import { RUN_STATE_QUERY_KEY } from "./useRunState";

export const TARGETS_QUERY_KEY = ["targets"] as const;

export type TargetWithSnapshot = TargetT & { snapshot: TargetSnapshotT | null };

export interface UseTargetsResult {
  targets: readonly TargetWithSnapshot[];
  isPending: boolean;
}

/**
 * Joins `["targets"]` (static fields) with `["run","state"].targets`
 * (per-target snapshot). Returns one merged object per target so tile
 * components can read both halves from a single source.
 *
 * Reviewer H-2: `queryClient.getQueryData` inside `useMemo` does NOT
 * re-run when the WS patches the snapshot cache (queryClient identity
 * is stable). Use a sibling `useQuery` against the same key — TanStack
 * dedupes the queryFn against `useRunState`'s call (both have
 * `staleTime: Infinity`), and a `setQueryData` from the WS patcher
 * notifies BOTH subscribers, so this hook re-renders on every snapshot.
 */
export function useTargets(): UseTargetsResult {
  const { data, isPending } = useQuery<readonly TargetT[], ApiError>({
    queryKey: TARGETS_QUERY_KEY,
    queryFn: () => apiFetch("targets", {}, TargetList),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const { data: runState } = useQuery<RunStateViewT, ApiError>({
    queryKey: RUN_STATE_QUERY_KEY,
    queryFn: () => apiFetch("run/state", {}, RunStateView),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });

  return useMemo(() => {
    if (!data) return { targets: [], isPending };
    const snapshotsById = new Map<string, TargetSnapshotT>();
    runState?.targets.forEach((s) => snapshotsById.set(s.target_id, s));
    const merged: TargetWithSnapshot[] = data.map((t) => ({
      ...t,
      snapshot: snapshotsById.get(t.id) ?? null,
    }));
    return { targets: merged, isPending };
  }, [data, isPending, runState]);
}
