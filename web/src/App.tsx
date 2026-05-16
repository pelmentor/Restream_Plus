import { useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { globalApiErrorBus } from "@/lib/api";

/**
 * Top-level API-error router. Subscribes once to `globalApiErrorBus`
 * and translates auth / locked errors into navigation. Login / Unlock
 * pages opt out via `silenceGlobalErrors: true` so they handle their
 * own 401 / 503 inline.
 *
 * Rendered as a sibling of <RouterProvider> at the root — `main.tsx`
 * passes the QueryClient + Router; this component lives inside the
 * QueryClient + Router context so the hooks resolve.
 */
export function GlobalErrorHandler(): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useEffect(() => {
    return globalApiErrorBus.subscribe((err) => {
      if (err.code === "unauthorized") {
        queryClient.setQueryData(["auth", "session"], null);
        // Defer to next tick so React commit can flush before the
        // navigation; avoids "setState during render" warnings on
        // simultaneous in-flight queries.
        queueMicrotask(() => {
          void navigate("/login", { replace: true });
        });
        return;
      }
      if (err.code === "service_locked" || err.code === "service_unlocking") {
        queueMicrotask(() => {
          void navigate("/unlock", { replace: true });
        });
        return;
      }
    });
  }, [navigate, queryClient]);

  return null;
}
