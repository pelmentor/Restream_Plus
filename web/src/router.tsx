/* eslint-disable react-refresh/only-export-components -- route registry exports a non-component `routes` array */
import { lazy, Suspense, type ReactNode } from "react";
import { Outlet, type RouteObject } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { AuthRepromptHost } from "@/components/AuthRepromptHost";
import { RecentEventsProvider } from "@/components/RecentEventsProvider";
import { RequireAuth } from "@/components/RequireAuth";
import { Skeleton } from "@/components/Skeleton";
import { StatusStreamHost } from "@/components/StatusStreamHost";
import { GlobalErrorHandler } from "@/App";
import { Dashboard } from "@/pages/Dashboard";
import { LoginPage } from "@/pages/Login";
import { NotFoundPage } from "@/pages/NotFound";
import { UnlockPage } from "@/pages/Unlock";

// Phase 9 — single lazy chunk for the entire Settings surface.
const LazySettings = lazy(() => import("@/pages/settings"));

function SettingsBoundary(): ReactNode {
  return (
    <Suspense fallback={<Skeleton.Tile />}>
      <LazySettings />
    </Suspense>
  );
}

/**
 * Routes for the SPA. The outer pseudo-route mounts the
 * GlobalErrorHandler so its hooks can resolve in the router context.
 */
export const routes: RouteObject[] = [
  {
    element: (
      <>
        <GlobalErrorHandler />
        <Outlet />
      </>
    ),
    children: [
      { path: "/login",  element: <LoginPage /> },
      { path: "/unlock", element: <UnlockPage /> },
      {
        element: (
          <RequireAuth>
            <AuthRepromptHost>
              <RecentEventsProvider>
                <StatusStreamHost />
                <AppShell />
              </RecentEventsProvider>
            </AuthRepromptHost>
          </RequireAuth>
        ),
        children: [
          { index: true,        element: <Dashboard /> },
          { path: "settings/*", element: <SettingsBoundary /> },
        ],
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
