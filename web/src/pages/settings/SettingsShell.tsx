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
  // Hex Audit UX-F11 (slice 10): on mobile (< md), the sidebar stacks
  // ABOVE the main content. With 9 nav rows it pushes the content
  // off-screen and forces the operator to scroll past the nav on every
  // tab change. Two production patterns considered: (a) hamburger that
  // hides the sidebar, (b) horizontal scroll-strip. The strip is the
  // smaller-surface fix — no new drawer component, no Radix DropdownMenu
  // dependency, no focus-trap interaction with the existing AppShell
  // header. The sidebar `<aside>` already lays out as a vertical column
  // on its own; the wrapper here gives it `overflow-x-auto +
  // max-w-full` on mobile so it scrolls horizontally while preserving
  // its desktop layout via the md:grid-cols-[…] media query.
  return (
    <div className="grid gap-(--space-8) md:grid-cols-[240px_minmax(0,1fr)]">
      <div className="md:contents max-md:-mx-(--space-4) max-md:overflow-x-auto max-md:px-(--space-4)">
        <SettingsSidebar />
      </div>
      <main className="min-w-0 max-w-(--width-content)" aria-label={t("settings.sidebarHeading")}>
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
