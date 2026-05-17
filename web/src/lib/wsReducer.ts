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
 *  - `target.snapshot` patches the matching target; on race with
 *    `["targets"]` invalidation it appends a stub.
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
      return {
        ...prev,
        run_state: event.data.new_state,
        run_state_changed_at: event.data.at,
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
  if (found) return out;
  // Race against ["targets"] invalidation — append a stub. Next
  // state.full reconciles fully.
  return [
    ...list,
    {
      target_id: next.target_id,
      ui_state: next.ui_state,
      snapshots_by_role: next.snapshots_by_role,
    },
  ];
}
