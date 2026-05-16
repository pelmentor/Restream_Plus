import { useSyncExternalStore } from "react";

import { statusStream, type WsConnectionStatus } from "@/lib/ws";

/**
 * Subscribes to the WS singleton's connection status. Multi-cast — any
 * number of components can call this; only the StatusStreamHost owns
 * the underlying socket lifecycle (per phase-8-design-memo §R).
 */
export function useWsConnectionStatus(): WsConnectionStatus {
  return useSyncExternalStore(
    (onChange) => statusStream.onStatus(onChange),
    () => statusStream.getStatus(),
    () => "idle",
  );
}
