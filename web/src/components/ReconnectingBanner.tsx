import { useEffect, useState, type ReactNode } from "react";

import { Banner } from "./Banner";
import { useWsConnectionStatus } from "@/hooks/useWsConnectionStatus";
import { t } from "@/messages";

/**
 * Per phase-8-design-memo §N + UX Q8: in-flow at the top of <main>,
 * NOT viewport-sticky. Appears on the first failed reconnect attempt
 * (not on every momentary blip), disappears on "open". Persistent (no
 * dismiss button).
 *
 * "First failed reconnect" detected via the WsConnectionStatus state
 * machine: the singleton emits "reconnecting" after the first failed
 * `open()` call. We render iff `connectionStatus === "reconnecting"`.
 */
export function ReconnectingBanner(): ReactNode {
  const status = useWsConnectionStatus();
  // Defer first paint by 600ms — a clean re-handshake usually
  // completes within the WS backoff base (1s) and we want to avoid
  // banner flash on transient blips.
  const [shouldShow, setShouldShow] = useState(false);

  useEffect(() => {
    if (status !== "reconnecting") {
      setShouldShow(false);
      return;
    }
    const handle = window.setTimeout(() => setShouldShow(true), 600);
    return () => window.clearTimeout(handle);
  }, [status]);

  if (!shouldShow) return null;
  return (
    <Banner variant="info" title={t("dashboard.reconnectingTitle")} className="mb-(--space-6)" />
  );
}
