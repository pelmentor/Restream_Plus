import type { ReactNode } from "react";

import { t } from "@/messages";
import { ThemeToggle } from "@/theme/ThemeToggle";

/**
 * Full-bleed centered layout for /login and /unlock. No header, no
 * skip link (the form is two tabs deep at most). ThemeToggle is
 * present so the user can pick a comfortable theme before signing in.
 */
export function AuthLayout({ children }: { children: ReactNode }): ReactNode {
  // Hex Audit UX-F10 (slice 10): `<main>` precedes ThemeToggle in DOM
  // order so the first Tab keystroke lands inside the auth form
  // (typically the username/password input via its `autoFocus`), not
  // on the ThemeToggle button. ThemeToggle is still keyboard-reachable
  // (last Tab stop) and stays visually pinned top-right via the
  // absolute positioning. Pre-slice-10 the toggle came FIRST in DOM,
  // which meant operators tabbing into a freshly-loaded login page
  // landed on the theme switcher rather than the credential field.
  return (
    <div className="min-h-screen grid place-items-center bg-(--color-bg-base) p-(--space-4)">
      <main className="w-full max-w-(--width-dialog-sm)">
        <div className="mb-(--space-6) text-center">
          <h1 className="text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
            {t("app.name")}
          </h1>
          <p className="mt-(--space-1) text-(length:--text-sm) text-(--color-fg-muted)">
            {t("app.tagline")}
          </p>
        </div>
        <div className="rounded-(--radius-lg) border border-(--color-border-subtle) bg-(--color-bg-elevated) p-(--space-8) shadow-(--shadow-md)">
          {children}
        </div>
      </main>
      <div className="absolute right-(--space-4) top-(--space-4)">
        <ThemeToggle />
      </div>
    </div>
  );
}
