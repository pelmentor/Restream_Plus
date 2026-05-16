/* eslint-disable react-refresh/only-export-components -- shell exports a small hook for nested tab status announcements */
import { type ReactNode } from "react";
import { Outlet } from "react-router-dom";

import { SettingsSidebar } from "@/components/settings/SettingsSidebar";
import { t } from "@/messages";

interface StatusAnnouncerCtx {
  announce: (msg: string) => void;
}

/**
 * Page-scoped status announcer. One `aria-live="polite"` region per
 * `<SettingsShell>` mount (phase-9-design-memo §Q.1). Falls back to a
 * no-op when the shell isn't mounted (test scaffolding).
 */
export function useSettingsStatusAnnouncer(): StatusAnnouncerCtx {
  return {
    announce: (msg: string) => {
      const region = document.getElementById("settings-status-region");
      if (region) {
        region.textContent = msg;
        setTimeout(() => {
          if (region.textContent === msg) region.textContent = "";
        }, 5000);
      }
    },
  };
}

/**
 * The Settings page chrome: 2-column grid on desktop (240 px sidebar +
 * 800 px main), single column on mobile.
 */
export function SettingsShell(): ReactNode {
  return (
    <div className="grid gap-(--space-8) md:grid-cols-[240px_minmax(0,1fr)]">
      <SettingsSidebar />
      <main className="min-w-0 max-w-[800px]" aria-label={t("settings.sidebarHeading")}>
        <div
          id="settings-status-region"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="sr-only"
        />
        <Outlet />
      </main>
    </div>
  );
}
