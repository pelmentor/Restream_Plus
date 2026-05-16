import { useEffect, useRef, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { statusStream } from "@/lib/ws";
import { applyWsEventToCache } from "@/lib/wsReducer";
import { RunStateView, type RunStateViewT, type TargetUiStateT } from "@/lib/schemas/run";
import { WsKnownEvent } from "@/lib/schemas/wsEnvelopes";
import { RUN_STATE_QUERY_KEY } from "@/hooks/useRunState";
import { useRecentEventsActions, type RecentEvent } from "@/components/RecentEventsProvider";

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

      // Edge-detect for RecentEventsMenu.
      const prevMap = prevUiStateRef.current;
      if (known.event === "state.full") {
        const validated = RunStateView.safeParse(known.data);
        if (validated.success) {
          validated.data.targets.forEach((t) => prevMap.set(t.target_id, t.ui_state));
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
  return {
    id: `${atIso}-${Math.random().toString(36).slice(2, 10)}`,
    kind,
    message,
    at: new Date(atIso),
  };
}
