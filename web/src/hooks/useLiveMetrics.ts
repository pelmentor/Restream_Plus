import { useQuery } from "@tanstack/react-query";

import {
  EMPTY_LIVE_METRICS,
  type LiveMetricsStateT,
} from "@/lib/liveMetricsReducer";

export const LIVE_METRICS_QUERY_KEY = ["live", "metrics"] as const;

/**
 * Reads the live-metrics cache (host stats + rolling sparkline buffers).
 * The cache is maintained by `StatusStreamHost` via
 * `applyLiveMetricsEvent`. Pure read here — no fetch, no retry; the
 * WS owns freshness. Empty seed state when the cache is cold so
 * components don't have to handle `undefined`.
 */
export function useLiveMetrics(): LiveMetricsStateT {
  const { data } = useQuery<LiveMetricsStateT>({
    queryKey: LIVE_METRICS_QUERY_KEY,
    // No queryFn — the cache is push-only. Provide `initialData` so
    // there's never an `undefined` window between mount and the first
    // WS event.
    initialData: EMPTY_LIVE_METRICS,
    staleTime: Infinity,
    gcTime: Infinity,
    enabled: false,
  });
  return data ?? EMPTY_LIVE_METRICS;
}
