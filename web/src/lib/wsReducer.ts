/**
 * Pure function that maps a known WS event onto the run-state cache.
 *
 * Extracted from the React hook so the highest-risk piece of Phase 8 is
 * testable in isolation (Vitest, environment: "node"). Per
 * phase-8-design-memo §F + §M.
 *
 * Invariants:
 *  - `state.full` is a full replace (it IS a RunStateView).
 *  - `run.state.changed` updates `run_state` + `run_state_changed_at`.
 *  - `target.snapshot` patches the matching target IN PLACE. Snapshots
 *    for unknown ids are dropped — the next `state.full` reconciles
 *    (the reducer never grows the targets array from snapshot events).
 *  - `bus.drop_alert` updates `dropped_total`.
 *  - All non-`state.full` patches tolerate `prev === undefined` by
 *    returning prev unchanged (seed not yet present — next state.full
 *    will fix it).
 */

import type { RunStateViewT, TargetSnapshotT } from "./schemas/run";
import type { WsKnownEventT } from "./schemas/wsEnvelopes";

export function applyWsEventToCache(
  prev: RunStateViewT | undefined,
  event: WsKnownEventT,
): RunStateViewT | undefined {
  switch (event.event) {
    case "state.full":
      return event.data;
    case "run.state.changed":
      if (prev === undefined) return prev;
      // OFFLINE = no workers running. Clear stale per-target snapshots
      // so tiles don't keep showing RECONNECTING forever after the
      // operator hits STOP. ERROR keeps the snapshots so the operator
      // can see WHICH target failed; STARTING / ARMED / LIVE / STOPPING
      // also keep them (workers are actively transitioning, snapshots
      // are the live signal). Only the OFFLINE terminal state is the
      // reset moment.
      return {
        ...prev,
        run_state: event.data.new_state,
        run_state_changed_at: event.data.at,
        // CR2-M1 (2026-05-18): reuse the existing reference when the
        // OFFLINE-reset is a no-op (already empty) so React `===`
        // memoization downstream isn't broken by a fresh `[]` literal
        // on repeated `offline → offline` transitions.
        targets:
          event.data.new_state === "offline"
            ? prev.targets.length === 0
              ? prev.targets
              : []
            : prev.targets,
      };
    case "target.snapshot":
      if (prev === undefined) return prev;
      return { ...prev, targets: patchTargetSnapshots(prev.targets, event.data) };
    case "bus.drop_alert":
      if (prev === undefined) return prev;
      return { ...prev, dropped_total: event.data.dropped_total };
  }
}

function patchTargetSnapshots(
  list: readonly TargetSnapshotT[],
  next: { target_id: string; ui_state: TargetSnapshotT["ui_state"]; snapshots_by_role: TargetSnapshotT["snapshots_by_role"] },
): readonly TargetSnapshotT[] {
  // Footgun-hunter F1 (Hex Audit 2026-05-18): the reducer must NEVER grow
  // the targets array from unsolicited events. An earlier "race against
  // ["targets"] invalidation" branch appended a stub for unknown ids,
  // which under a snapshot flood with rotating fresh ids let the array
  // grow unbounded (O(N) per render → OOM). Same class as the v1.1.3
  // SPA route-compounding crash. Invariant now: snapshots for unknown
  // ids are discarded; the next `state.full` reconciles.
  let found = false;
  const out = list.map((t) => {
    if (t.target_id !== next.target_id) return t;
    found = true;
    return {
      target_id: t.target_id,
      ui_state: next.ui_state,
      snapshots_by_role: next.snapshots_by_role,
    };
  });
  return found ? out : list;
}
