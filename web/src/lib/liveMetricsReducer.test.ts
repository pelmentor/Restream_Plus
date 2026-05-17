import { describe, expect, it } from "vitest";

import {
  applyLiveMetricsEvent,
  EMPTY_LIVE_METRICS,
  type LiveMetricsStateT,
} from "./liveMetricsReducer";
import type { WsKnownEventT } from "./schemas/wsEnvelopes";

const NOW = "2026-05-17T12:00:00+00:00";
const LATER = "2026-05-17T12:00:02+00:00";

function progress(bitrate_kbps: number, drop_frames = 0) {
  return {
    bitrate_kbps,
    fps: 30,
    drop_frames,
    speed: 1.0,
    at: NOW,
  };
}

describe("applyLiveMetricsEvent", () => {
  it("target.snapshot appends to per-target buffer + recomputes aggregate", () => {
    const ev: WsKnownEventT = {
      event: "target.snapshot",
      data: {
        target_id: "t1",
        ui_state: "running",
        snapshots_by_role: [
          {
            role: "primary",
            state: "running",
            last_event_at: NOW,
            last_error: null,
            breaker_failures_in_window: 0,
            last_progress: progress(4500),
          },
        ],
        at: NOW,
      },
    };
    const next = applyLiveMetricsEvent(EMPTY_LIVE_METRICS, ev);
    expect(next.egressByTarget.get("t1")).toEqual([
      { bitrate: 4500, healthy: true },
    ]);
    expect(next.aggregateBuffer).toEqual([
      { bitrate: 4500, healthy: true },
    ]);
  });

  it("target.snapshot without last_progress preserves previous buffers", () => {
    const seed: LiveMetricsStateT = {
      hostStats: null,
      egressByTarget: new Map([["t1", [{ bitrate: 4500, healthy: true }]]]),
      aggregateBuffer: [{ bitrate: 4500, healthy: true }],
      ingestBuffer: [],
    };
    const ev: WsKnownEventT = {
      event: "target.snapshot",
      data: {
        target_id: "t1",
        ui_state: "running",
        snapshots_by_role: [
          {
            role: "primary",
            state: "running",
            last_event_at: NOW,
            last_error: null,
            breaker_failures_in_window: 0,
            // No last_progress — state-only update.
          },
        ],
        at: NOW,
      },
    };
    const next = applyLiveMetricsEvent(seed, ev);
    expect(next.egressByTarget.get("t1")).toEqual([
      { bitrate: 4500, healthy: true },
    ]);
    expect(next.aggregateBuffer).toEqual([{ bitrate: 4500, healthy: true }]);
  });

  it("target.snapshot from a non-running worker marks the sample unhealthy", () => {
    const ev: WsKnownEventT = {
      event: "target.snapshot",
      data: {
        target_id: "t1",
        ui_state: "errored",
        snapshots_by_role: [
          {
            role: "primary",
            state: "reconnecting",
            last_event_at: NOW,
            last_error: "transient",
            breaker_failures_in_window: 1,
            last_progress: progress(1200),
          },
        ],
        at: NOW,
      },
    };
    const next = applyLiveMetricsEvent(EMPTY_LIVE_METRICS, ev);
    expect(next.egressByTarget.get("t1")?.[0]).toEqual({
      bitrate: 1200,
      healthy: false,
    });
    expect(next.aggregateBuffer[0]?.healthy).toBe(false);
  });

  it("aggregate is the sum of per-target latest samples, not a running sum", () => {
    // Two targets each at 5000 kbps. Aggregate should be 10000, NOT
    // 5000 + 10000 if we naively accumulated.
    const seed = applyLiveMetricsEvent(EMPTY_LIVE_METRICS, {
      event: "target.snapshot",
      data: {
        target_id: "t1",
        ui_state: "running",
        snapshots_by_role: [
          {
            role: "primary",
            state: "running",
            last_event_at: NOW,
            last_error: null,
            breaker_failures_in_window: 0,
            last_progress: progress(5000),
          },
        ],
        at: NOW,
      },
    });
    const next = applyLiveMetricsEvent(seed, {
      event: "target.snapshot",
      data: {
        target_id: "t2",
        ui_state: "running",
        snapshots_by_role: [
          {
            role: "primary",
            state: "running",
            last_event_at: NOW,
            last_error: null,
            breaker_failures_in_window: 0,
            last_progress: progress(5000),
          },
        ],
        at: NOW,
      },
    });
    const latest = next.aggregateBuffer[next.aggregateBuffer.length - 1];
    expect(latest?.bitrate).toBe(10000);
  });

  it("host.stats replaces hostStats and appends to ingest buffer when value is non-null", () => {
    const ev: WsKnownEventT = {
      event: "host.stats",
      data: {
        cpu_total_pct: 18.5,
        cpu_by_target: [
          { target_id: "t1", role: "primary", cpu_pct: 12.0 },
        ],
        rss_bytes: 100_000_000,
        ingest_kbps: 6000,
        at: NOW,
      },
    };
    const next = applyLiveMetricsEvent(EMPTY_LIVE_METRICS, ev);
    expect(next.hostStats?.cpu_total_pct).toBe(18.5);
    expect(next.ingestBuffer).toEqual([{ bitrate: 6000, healthy: true }]);
  });

  it("host.stats with ingest_kbps=null does NOT push a 0 into the ingest buffer", () => {
    // 0 ingest_kbps would lie about a dark show; None means unavailable.
    const seed: LiveMetricsStateT = {
      ...EMPTY_LIVE_METRICS,
      ingestBuffer: [{ bitrate: 6000, healthy: true }],
    };
    const ev: WsKnownEventT = {
      event: "host.stats",
      data: {
        cpu_total_pct: 5,
        cpu_by_target: [],
        rss_bytes: 4096,
        ingest_kbps: null,
        at: NOW,
      },
    };
    const next = applyLiveMetricsEvent(seed, ev);
    expect(next.ingestBuffer).toEqual([{ bitrate: 6000, healthy: true }]);
    expect(next.hostStats?.ingest_kbps).toBeNull();
  });

  it("run.state.changed to offline clears all buffers", () => {
    const seed: LiveMetricsStateT = {
      hostStats: {
        cpu_total_pct: 10,
        cpu_by_target: [],
        rss_bytes: 4096,
        ingest_kbps: 6000,
        at: NOW,
      },
      egressByTarget: new Map([["t1", [{ bitrate: 5000, healthy: true }]]]),
      aggregateBuffer: [{ bitrate: 5000, healthy: true }],
      ingestBuffer: [{ bitrate: 6000, healthy: true }],
    };
    const ev: WsKnownEventT = {
      event: "run.state.changed",
      data: {
        new_state: "offline",
        previous_state: "stopping",
        cause: "user stop",
        at: LATER,
      },
    };
    expect(applyLiveMetricsEvent(seed, ev)).toEqual(EMPTY_LIVE_METRICS);
  });

  it("run.state.changed to non-offline preserves buffers (operator may still be reading the chart)", () => {
    const seed: LiveMetricsStateT = {
      ...EMPTY_LIVE_METRICS,
      aggregateBuffer: [{ bitrate: 5000, healthy: true }],
    };
    const ev: WsKnownEventT = {
      event: "run.state.changed",
      data: {
        new_state: "stopping",
        previous_state: "live",
        cause: "user stop",
        at: LATER,
      },
    };
    const next = applyLiveMetricsEvent(seed, ev);
    expect(next.aggregateBuffer).toEqual(seed.aggregateBuffer);
  });

  it("state.full seeds per-target buffers from last_progress on snapshots", () => {
    const ev: WsKnownEventT = {
      event: "state.full",
      data: {
        run_state: "live",
        targets: [
          {
            target_id: "t1",
            ui_state: "running",
            snapshots_by_role: [
              {
                role: "primary",
                state: "running",
                last_event_at: NOW,
                last_error: null,
                breaker_failures_in_window: 0,
                last_progress: progress(4500),
              },
            ],
          },
        ],
        heartbeat_age_seconds: 0.5,
        dropped_total: 0,
        run_state_changed_at: NOW,
      },
    };
    const next = applyLiveMetricsEvent(EMPTY_LIVE_METRICS, ev);
    expect(next.egressByTarget.get("t1")).toEqual([
      { bitrate: 4500, healthy: true },
    ]);
    expect(next.aggregateBuffer).toEqual([{ bitrate: 4500, healthy: true }]);
  });

  it("buffer is capped at 60 samples", () => {
    let state: LiveMetricsStateT = EMPTY_LIVE_METRICS;
    for (let i = 0; i < 70; i++) {
      state = applyLiveMetricsEvent(state, {
        event: "host.stats",
        data: {
          cpu_total_pct: i,
          cpu_by_target: [],
          rss_bytes: 4096,
          ingest_kbps: i * 100,
          at: NOW,
        },
      });
    }
    expect(state.ingestBuffer).toHaveLength(60);
    // Oldest entries dropped: first sample should be sample 10 (0-indexed),
    // i.e. ingest_kbps = 10*100 = 1000.
    expect(state.ingestBuffer[0]?.bitrate).toBe(1000);
    expect(state.ingestBuffer[59]?.bitrate).toBe(6900);
  });
});
