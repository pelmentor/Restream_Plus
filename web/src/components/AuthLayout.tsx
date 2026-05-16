import type { ReactNode } from "react";

import { t } from "@/messages";
import { ThemeToggle } from "@/theme/ThemeToggle";

/**
 * Full-bleed centered layout for /login and /unlock. No header, no
 * skip link (the form is two tabs deep at most). ThemeToggle is
 * present so the user can pick a comfortable theme before signing in.
 */
export function AuthLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <div className="min-h-screen grid place-items-center bg-(--color-bg-base) p-(--space-4)">
      <div className="absolute right-(--space-4) top-(--space-4)">
        <ThemeToggle />
      </div>
      <main className="w-full max-w-[360px]">
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
    </div>
  );
}
