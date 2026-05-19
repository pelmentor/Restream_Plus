import { describe, expect, it } from "vitest";

import { applyWsEventToCache } from "./wsReducer";
import type { RunStateViewT } from "./schemas/run";
import type { WsKnownEventT } from "./schemas/wsEnvelopes";

const NOW = "2026-05-16T12:34:56+00:00";
const LATER = "2026-05-16T12:35:00+00:00";

const seed: RunStateViewT = {
  run_state: "offline",
  targets: [
    {
      target_id: "t1",
      ui_state: "idle",
      snapshots_by_role: [],
    },
  ],
  heartbeat_age_seconds: 0.2,
  dropped_total: 0,
  run_state_changed_at: NOW,
};

describe("applyWsEventToCache", () => {
  it("state.full replaces the cache fully", () => {
    const fullEvent: WsKnownEventT = {
      event: "state.full",
      data: { ...seed, run_state: "live", run_state_changed_at: LATER },
    };
    const next = applyWsEventToCache(seed, fullEvent);
    expect(next?.run_state).toBe("live");
    expect(next?.run_state_changed_at).toBe(LATER);
  });

  it("state.full populates an empty cache (cold-boot race)", () => {
    const fullEvent: WsKnownEventT = { event: "state.full", data: seed };
    expect(applyWsEventToCache(undefined, fullEvent)).toEqual(seed);
  });

  it("run.state.changed to a non-offline state patches run_state and run_state_changed_at, keeps targets", () => {
    const ev: WsKnownEventT = {
      event: "run.state.changed",
      data: {
        new_state: "live",
        previous_state: "armed",
        cause: "OBS started publishing",
        at: LATER,
      },
    };
    const next = applyWsEventToCache(seed, ev);
    expect(next?.run_state).toBe("live");
    expect(next?.run_state_changed_at).toBe(LATER);
    // Targets array preserved during STARTING/ARMED/LIVE/STOPPING/ERROR
    // — they're the live signal for the operator.
    expect(next?.targets).toBe(seed.targets);
  });

  it("run.state.changed offline→offline preserves the targets[] reference (CR2-M1)", () => {
    // Hex Audit CR2-M1: the OFFLINE reset must reuse the existing
    // `prev.targets` reference when it's already empty, so React `===`
    // memoization downstream isn't broken by a fresh `[]` literal on
    // repeated offline events.
    const emptySeed: RunStateViewT = { ...seed, targets: [] };
    const ev: WsKnownEventT = {
      event: "run.state.changed",
      data: {
        new_state: "offline",
        previous_state: "offline",
        cause: "redundant offline event",
        at: LATER,
      },
    };
    const next = applyWsEventToCache(emptySeed, ev);
    expect(next?.targets).toBe(emptySeed.targets);
  });

  it("run.state.changed to OFFLINE clears stale target snapshots", () => {
    // Without this, a target whose worker died with ui_state="errored"
    // shows RECONNECTING forever after the operator hits STOP — the
    // tile's snapshot is never updated, so it keeps rendering the
    // pre-stop state. Resetting on the OFFLINE transition is the
    // semantic source of truth: no run → no workers → no snapshots.
    const seedWithBusyTargets: RunStateViewT = {
      ...seed,
      run_state: "live",
      targets: [
        { target_id: "t1", ui_state: "errored", snapshots_by_role: [] },
        { target_id: "t2", ui_state: "running", snapshots_by_role: [] },
      ],
    };
    const ev: WsKnownEventT = {
      event: "run.state.changed",
      data: {
        new_state: "offline",
        previous_state: "stopping",
        cause: "operator STOP",
        at: LATER,
      },
    };
    const next = applyWsEventToCache(seedWithBusyTargets, ev);
    expect(next?.run_state).toBe("offline");
    expect(next?.targets).toEqual([]);
  });

  it("run.state.changed on undefined prev is a no-op", () => {
    const ev: WsKnownEventT = {
      event: "run.state.changed",
      data: {
        new_state: "live",
        previous_state: "armed",
        cause: "",
        at: LATER,
      },
    };
    expect(applyWsEventToCache(undefined, ev)).toBeUndefined();
  });

  it("target.snapshot patches the matching target in place", () => {
    const ev: WsKnownEventT = {
      event: "target.snapshot",
      data: {
        target_id: "t1",
        ui_state: "running",
        snapshots_by_role: [],
        at: LATER,
      },
    };
    const next = applyWsEventToCache(seed, ev);
    expect(next?.targets[0]?.ui_state).toBe("running");
    expect(next?.run_state).toBe(seed.run_state);
  });

  it("target.snapshot for unknown target is dropped — reducer never grows targets[] from snapshots", () => {
    // Footgun-hunter F1: an earlier stub-append branch let the array
    // grow unbounded under a snapshot flood with rotating fresh ids
    // (same class as v1.1.3 SPA route compounding). Invariant now: only
    // `state.full` populates new entries; snapshot events for unknown
    // ids are discarded.
    const ev: WsKnownEventT = {
      event: "target.snapshot",
      data: {
        target_id: "t-new",
        ui_state: "starting",
        snapshots_by_role: [],
        at: LATER,
      },
    };
    const next = applyWsEventToCache(seed, ev);
    expect(next?.targets).toHaveLength(1);
    expect(next?.targets[0]?.target_id).toBe("t1");
  });

  it("target.snapshot flood with rotating unknown ids does not grow targets[]", () => {
    let state: RunStateViewT | undefined = seed;
    for (let i = 0; i < 1000; i++) {
      state = applyWsEventToCache(state, {
        event: "target.snapshot",
        data: {
          target_id: `random-${i}`,
          ui_state: "running",
          snapshots_by_role: [],
          at: LATER,
        },
      });
    }
    expect(state?.targets).toHaveLength(1);
    expect(state?.targets[0]?.target_id).toBe("t1");
  });

  it("target.snapshot on undefined prev is a no-op", () => {
    const ev: WsKnownEventT = {
      event: "target.snapshot",
      data: {
        target_id: "anything",
        ui_state: "running",
        snapshots_by_role: [],
        at: LATER,
      },
    };
    expect(applyWsEventToCache(undefined, ev)).toBeUndefined();
  });

  it("bus.drop_alert patches dropped_total only", () => {
    const ev: WsKnownEventT = {
      event: "bus.drop_alert",
      data: { dropped_total: 42, at: LATER },
    };
    const next = applyWsEventToCache(seed, ev);
    expect(next?.dropped_total).toBe(42);
    expect(next?.run_state).toBe(seed.run_state);
    expect(next?.targets).toBe(seed.targets);
  });

  it("bus.drop_alert on undefined prev is a no-op", () => {
    const ev: WsKnownEventT = {
      event: "bus.drop_alert",
      data: { dropped_total: 1, at: LATER },
    };
    expect(applyWsEventToCache(undefined, ev)).toBeUndefined();
  });
});
