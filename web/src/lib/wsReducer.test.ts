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

  it("run.state.changed patches run_state and run_state_changed_at", () => {
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
    // Targets array preserved.
    expect(next?.targets).toBe(seed.targets);
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

  it("target.snapshot for unknown target appends a stub (race tolerance)", () => {
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
    expect(next?.targets).toHaveLength(2);
    expect(next?.targets[1]?.target_id).toBe("t-new");
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
