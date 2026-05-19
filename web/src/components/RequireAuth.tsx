import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { UserSummary, type UserSummaryT } from "@/lib/schemas/auth";
import { Skeleton } from "@/components/Skeleton";

const SESSION_QUERY_KEY = ["auth", "session"] as const;

/**
 * Pre-mount auth gate. Hits `GET /api/auth/me` once; on 200 renders
 * children, on 401 navigates to /login, on 503 service_locked /
 * service_unlocking navigates to /unlock.
 *
 * The `state.from` preserved in the redirect is consumed by Login
 * (and by Unlock) via `safeNext`.
 */
export function RequireAuth({ children }: { children: ReactNode }): ReactNode {
  const location = useLocation();
  const { data, error, isPending } = useQuery<UserSummaryT, ApiError>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: () => apiFetch("auth/me", { silenceGlobalErrors: true }, UserSummary),
    // meta.silenceGlobalErrors — review M-1: this query handles 401 /
    // 503 itself by routing to /login or /unlock; the global handler
    // must not race the inline <Navigate>.
    meta: { silenceGlobalErrors: true },
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
  });

  if (isPending) {
    return (
      <div className="min-h-screen grid place-items-center p-(--space-4)">
        <div className="w-full max-w-(--width-dialog-md) space-y-4">
          <Skeleton.Row className="h-6 w-1/2" />
          <Skeleton.Tile />
        </div>
      </div>
    );
  }

  if (error) {
    const from = location.pathname + location.search;
    if (error.code === "service_locked" || error.code === "service_unlocking") {
      return <Navigate to="/unlock" replace state={{ from }} />;
    }
    return <Navigate to="/login" replace state={{ from }} />;
  }

  if (!data) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  return <>{children}</>;
}
