import { useEffect, useRef, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { statusStream } from "@/lib/ws";
import { applyWsEventToCache } from "@/lib/wsReducer";
import {
  applyLiveMetricsEvent,
  EMPTY_LIVE_METRICS,
  type LiveMetricsStateT,
} from "@/lib/liveMetricsReducer";
import { RunStateView, type RunStateViewT, type TargetUiStateT } from "@/lib/schemas/run";
import { WsKnownEvent } from "@/lib/schemas/wsEnvelopes";
import { RUN_STATE_QUERY_KEY } from "@/hooks/useRunState";
import { LIVE_METRICS_QUERY_KEY } from "@/hooks/useLiveMetrics";
import { useRecentEventsActions, type RecentEvent } from "@/components/RecentEventsProvider";

// Hex Audit footgun-hunter FG2-C1 (2026-05-18): cap the edge-detect
// Map. A `target.snapshot` event with a fresh `target_id` would
// otherwise grow the Map without bound — same v1.1.3 unbounded-growth
// class slice 1 closed in wsReducer + liveMetricsReducer (which uses
// the same TARGET_MAP_CAP = 16). Matches that value so the per-target
// tracking surface is uniform across the three client-side maps.
const PREV_UI_STATE_MAP_CAP = 16;

/**
 * Owns the WS singleton lifecycle for the authenticated session.
 * Mounted as a sibling of <AppShell> inside <RequireAuth> per
 * phase-8-design-memo §R. Renders nothing.
 *
 * Responsibilities:
 *  1. connect() on mount, disconnect() on unmount.
 *  2. Subscribe to envelopes, Zod-validate, and patch the run-state
 *     cache via `applyWsEventToCache`. This is the SOLE writer to
 *     `["run","state"]` outside its own queryFn (must-not-get-wrong
 *     §W#2).
 *  3. Edge-detect target-state transitions and dispatch matching
 *     entries into the RecentEvents store.
 */
export function StatusStreamHost(): ReactNode {
  const queryClient = useQueryClient();
  const { push } = useRecentEventsActions();
  // Track the prior ui_state per target so we only log transitions
  // (not every snapshot at RUNNING).
  const prevUiStateRef = useRef<Map<string, TargetUiStateT>>(new Map());

  useEffect(() => {
    statusStream.connect();
    return () => statusStream.disconnect();
  }, []);

  useEffect(() => {
    const unsub = statusStream.subscribe((envelope) => {
      const parsed = WsKnownEvent.safeParse({
        event: envelope.event,
        data: envelope.data,
      });
      if (!parsed.success) return;
      const known = parsed.data;

      queryClient.setQueryData<RunStateViewT | undefined>(
        RUN_STATE_QUERY_KEY,
        (prev) => applyWsEventToCache(prev, known),
      );

      // Parallel reducer for live metrics (host CPU + sparkline buffers).
      // Separate cache key because the buffers grow over time and don't
      // belong in the run-state shape (see phase-12 stats memo).
      queryClient.setQueryData<LiveMetricsStateT>(
        LIVE_METRICS_QUERY_KEY,
        (prev) => applyLiveMetricsEvent(prev ?? EMPTY_LIVE_METRICS, known),
      );

      // Edge-detect for RecentEventsMenu.
      const prevMap = prevUiStateRef.current;
      if (known.event === "state.full") {
        const validated = RunStateView.safeParse(known.data);
        if (validated.success) {
          // state.full is the server-side authoritative reset point.
          // Replace the Map wholesale (instead of incremental .set) so
          // operator-config target removals shrink the tracking surface;
          // bounded by validated.data.targets.length which is the
          // operator's target count.
          const replacement = new Map<string, TargetUiStateT>();
          validated.data.targets.forEach((t) =>
            replacement.set(t.target_id, t.ui_state),
          );
          prevUiStateRef.current = replacement;
        }
        return;
      }
      if (known.event === "run.state.changed") {
        if (known.data.new_state === "error") {
          push(makeEvent("error", "Run entered ERROR state.", known.data.at));
        } else if (
          known.data.new_state === "live" &&
          known.data.previous_state === "error"
        ) {
          push(makeEvent("success", "Recovered to LIVE.", known.data.at));
        }
        return;
      }
      if (known.event === "target.snapshot") {
        const prevUi = prevMap.get(known.data.target_id);
        const nextUi = known.data.ui_state;
        // FG2-C1: cap on insert of a NEW target_id. Map iteration
        // order in JS is insertion order, so `keys().next().value`
        // is the oldest entry — drop it to make room.
        if (
          !prevMap.has(known.data.target_id) &&
          prevMap.size >= PREV_UI_STATE_MAP_CAP
        ) {
          const oldestKey = prevMap.keys().next().value;
          if (oldestKey !== undefined) {
            prevMap.delete(oldestKey);
          }
        }
        prevMap.set(known.data.target_id, nextUi);
        if (prevUi === undefined) return;
        if (prevUi === nextUi) return;
        if (nextUi === "errored" || nextUi === "failed_open") {
          push(
            makeEvent(
              "error",
              `Target ${known.data.target_id} → ${nextUi.toUpperCase()}.`,
              known.data.at,
            ),
          );
        } else if (
          nextUi === "running" &&
          (prevUi === "errored" || prevUi === "failed_open" || prevUi === "degraded")
        ) {
          push(
            makeEvent(
              "success",
              `Target ${known.data.target_id} recovered.`,
              known.data.at,
            ),
          );
        }
        return;
      }
      if (known.event === "bus.drop_alert") {
        push(
          makeEvent(
            "warn",
            `Event bus dropped ${known.data.dropped_total} events.`,
            known.data.at,
          ),
        );
      }
    });
    return unsub;
  }, [queryClient, push]);

  return null;
}

function makeEvent(
  kind: RecentEvent["kind"],
  message: string,
  atIso: string,
): RecentEvent {
  // Hex Audit FG2-L2 (slice 10): `crypto.randomUUID()` instead of
  // `Math.random()`. Collision probability was already negligible
  // (8 base36 chars + a timestamp prefix), but `randomUUID` is the
  // built-in cryptographically-random primitive available in every
  // modern browser AND in jsdom (used by vitest) — using it removes
  // the "negligible but non-zero" footgun the audit flagged and
  // matches the rest of the codebase's identifier-generation pattern
  // (the API tokens and reprompt grant IDs both use a CSPRNG source
  // on the server side).
  return {
    id: `${atIso}-${crypto.randomUUID()}`,
    kind,
    message,
    at: new Date(atIso),
  };
}
