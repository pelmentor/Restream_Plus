/**
 * Pure reducer for the live-metrics cache: per-target egress bitrate
 * buffers + ingest buffer + last `host.stats` snapshot.
 *
 * Distinct from `wsReducer` so the existing `RunStateViewT` shape stays
 * load-bearing for the run-state cache (one cache = one shape) — the
 * metric buffers grow over time and don't belong in the run-state view.
 *
 * Buffer semantics:
 *   - 60 samples per target (60 s at the ~1 Hz target.snapshot cadence).
 *   - 60 samples of ingest (60 s at the 2 s host.stats tick = 120 s
 *     window; but the operator's eye is fine with 60 samples regardless
 *     of exact wall clock — the Sparkline component renders width-fitted).
 *   - 60 samples of AGGREGATE per-target egress (sum on each
 *     target.snapshot tick). The hero card consumes this.
 *
 * On `state.full` we RESET all buffers — a reconnect means we don't
 * have continuous history to glue across. On `run.state.changed` to
 * offline we clear buffers — the show ended.
 */

import type { SparklineSample } from "@/components/Sparkline";
import type { RunStateViewT } from "./schemas/run";
import type {
  HostStatsDataT,
  TargetSnapshotDataT,
  WsKnownEventT,
} from "./schemas/wsEnvelopes";

const BUFFER_CAP = 60;
/**
 * Defensive cap on the per-target buffer map. v1 has at most 4 targets,
 * but a malformed backend stream or a dynamic-target future could flood
 * novel `target_id`s into the map without ever clearing them (reset
 * only fires on `state.full` and on transition to `offline`). Cap +
 * FIFO eviction here keeps the worst-case bounded.
 */
const TARGET_MAP_CAP = 16;

export interface LiveMetricsStateT {
  readonly hostStats: HostStatsDataT | null;
  /** Per-target last-N egress samples, keyed by target_id. */
  readonly egressByTarget: ReadonlyMap<string, readonly SparklineSample[]>;
  /** Last-N aggregate egress samples (sum across enabled targets). */
  readonly aggregateBuffer: readonly SparklineSample[];
  /** Last-N ingest samples from host.stats. */
  readonly ingestBuffer: readonly SparklineSample[];
}

export const EMPTY_LIVE_METRICS: LiveMetricsStateT = {
  hostStats: null,
  egressByTarget: new Map(),
  aggregateBuffer: [],
  ingestBuffer: [],
};

export function applyLiveMetricsEvent(
  prev: LiveMetricsStateT | undefined,
  event: WsKnownEventT,
): LiveMetricsStateT {
  const base = prev ?? EMPTY_LIVE_METRICS;
  switch (event.event) {
    case "state.full":
      // Reconnect / cold boot. Seed per-target buffers with the
      // last_progress samples carried in the snapshot (one sample each)
      // so the operator sees SOMETHING before the next tick lands.
      return seedFromStateFull(base, event.data);
    case "target.snapshot":
      return applyTargetSnapshot(base, event.data);
    case "host.stats":
      return applyHostStats(base, event.data);
    case "run.state.changed":
      // Going OFFLINE clears the show's history. Other transitions
      // preserve buffers (e.g., LIVE → STOPPING shouldn't drop the
      // last 60s of the chart that the operator may still be reading).
      if (event.data.new_state === "offline") {
        return EMPTY_LIVE_METRICS;
      }
      return base;
    case "bus.drop_alert":
      // Drop alerts don't carry metric data.
      return base;
  }
}

function seedFromStateFull(
  base: LiveMetricsStateT,
  data: RunStateViewT,
): LiveMetricsStateT {
  const nextEgress = new Map<string, readonly SparklineSample[]>();
  let aggregate = 0;
  let aggregateHealthy = true;
  for (const target of data.targets) {
    const primary = target.snapshots_by_role.find((s) => s.role === "primary");
    if (primary?.last_progress == null) continue;
    const sample: SparklineSample = {
      bitrate: primary.last_progress.bitrate_kbps,
      healthy: primary.state === "running",
    };
    nextEgress.set(target.target_id, [sample]);
    aggregate += primary.last_progress.bitrate_kbps;
    if (primary.state !== "running") aggregateHealthy = false;
  }
  const aggregateBuffer: readonly SparklineSample[] =
    nextEgress.size > 0 ? [{ bitrate: aggregate, healthy: aggregateHealthy }] : [];
  return {
    hostStats: base.hostStats,
    egressByTarget: nextEgress,
    aggregateBuffer,
    ingestBuffer: base.ingestBuffer,
  };
}

function applyTargetSnapshot(
  base: LiveMetricsStateT,
  data: TargetSnapshotDataT,
): LiveMetricsStateT {
  const primary = data.snapshots_by_role.find((s) => s.role === "primary");
  // No primary snapshot or no progress → don't churn the buffer just
  // because of a state-only update. The previous sample stays valid.
  if (primary?.last_progress == null) return base;
  const sample: SparklineSample = {
    bitrate: primary.last_progress.bitrate_kbps,
    healthy: primary.state === "running",
  };
  // Per-target buffer. FIFO-evict if the map has hit TARGET_MAP_CAP
  // and this is a NEW target_id. Map iteration order in JS is
  // insertion order, so `Map.keys().next().value` is the oldest entry.
  const nextEgress = new Map<string, readonly SparklineSample[]>(base.egressByTarget);
  if (!nextEgress.has(data.target_id) && nextEgress.size >= TARGET_MAP_CAP) {
    const oldestKey = nextEgress.keys().next().value;
    if (oldestKey !== undefined) {
      nextEgress.delete(oldestKey);
    }
  }
  const prevBuf = nextEgress.get(data.target_id) ?? [];
  nextEgress.set(data.target_id, pushCapped(prevBuf, sample));

  // Aggregate = sum of the most recent sample per target. We can't just
  // add `sample` to the previous aggregate (that would double-count if
  // the same target ticks twice before any other does) — recompute from
  // the "head" of each per-target buffer.
  let aggregateBitrate = 0;
  let aggregateHealthy = true;
  for (const [, buf] of nextEgress) {
    const head = buf[buf.length - 1];
    if (head === undefined) continue;
    aggregateBitrate += head.bitrate;
    if (!head.healthy) aggregateHealthy = false;
  }
  const nextAggregate = pushCapped(base.aggregateBuffer, {
    bitrate: aggregateBitrate,
    healthy: aggregateHealthy,
  });

  return {
    hostStats: base.hostStats,
    egressByTarget: nextEgress,
    aggregateBuffer: nextAggregate,
    ingestBuffer: base.ingestBuffer,
  };
}

function applyHostStats(
  base: LiveMetricsStateT,
  data: HostStatsDataT,
): LiveMetricsStateT {
  // Ingest buffer — only push when we have a real value. None means
  // "unavailable" (no publisher / nginx wedged); don't paint a 0 into
  // the sparkline.
  let ingestBuffer = base.ingestBuffer;
  if (data.ingest_kbps !== null) {
    ingestBuffer = pushCapped(base.ingestBuffer, {
      bitrate: data.ingest_kbps,
      healthy: true,
    });
  }
  return {
    hostStats: data,
    egressByTarget: base.egressByTarget,
    aggregateBuffer: base.aggregateBuffer,
    ingestBuffer,
  };
}

function pushCapped<T>(
  buf: readonly T[],
  sample: T,
  cap: number = BUFFER_CAP,
): readonly T[] {
  if (buf.length < cap) return [...buf, sample];
  // Drop the oldest, append the new. Linear-time copy is fine at
  // cap=60 and ~1 Hz; no need for a ring-buffer abstraction.
  return [...buf.slice(buf.length - cap + 1), sample];
}
