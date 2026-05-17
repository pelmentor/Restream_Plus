import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CaretDown, UserCircle } from "@phosphor-icons/react";

import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/cn";
import { t } from "@/messages";
import { ThemeToggle } from "@/theme/ThemeToggle";
import { LiveStatsStrip } from "@/components/LiveStatsStrip";
import { RecentEventsMenu } from "@/components/RecentEventsMenu";
import { RunStateBadge } from "@/components/RunStateBadge";

const BUILD_SHA: string =
  typeof import.meta.env.VITE_BUILD_SHA === "string" && import.meta.env.VITE_BUILD_SHA.length > 0
    ? import.meta.env.VITE_BUILD_SHA
    : "dev";

/**
 * The post-auth route shell. Header + main + footer.
 *
 * - Skip link as the first focusable element.
 * - `<main id="main-content" tabIndex={-1}>` is the focus target after
 *   route changes so screen-reader users land on the new page.
 * - A SEPARATE `aria-live="polite"` page-title announcer; the run-state
 *   badge owns the other live region (one per concern, never two for
 *   the same event — design-system §9).
 * - The run-state badge is a static "OFFLINE" placeholder in Phase 7;
 *   it owns the live region from day one so Phase 8 inherits the
 *   contract.
 */
export function AppShell(): ReactNode {
  const location = useLocation();
  const mainRef = useRef<HTMLElement | null>(null);
  const [pageTitle, setPageTitle] = useState<string>("");

  useEffect(() => {
    mainRef.current?.focus();
    // Page title derives from <title> set by individual pages, falling
    // back to the route segment.
    const titleEl = document.querySelector("title");
    const title = titleEl?.textContent ?? location.pathname;
    setPageTitle(title);
  }, [location.pathname]);

  return (
    <>
      <a href="#main-content" className="skip-link">
        {t("common.skipToContent")}
      </a>
      <div className="min-h-screen flex flex-col bg-(--color-bg-base) text-(--color-fg-default)">
        <Header />
        <main
          id="main-content"
          ref={mainRef}
          tabIndex={-1}
          className="flex-1 mx-auto w-full max-w-[1200px] px-(--space-4) py-(--space-6) outline-none"
        >
          <Outlet />
        </main>
        <Footer />
        {/* Page-title announcer — distinct from the run-state badge's live region. */}
        <div className="sr-only" aria-live="polite" aria-atomic="true">
          {pageTitle}
        </div>
      </div>
    </>
  );
}

function Header(): ReactNode {
  return (
    <header className="sticky top-0 z-10 h-16 border-b border-(--color-border-subtle) bg-(--color-bg-base)/95 backdrop-blur">
      <div className="mx-auto flex h-full w-full max-w-[1200px] items-center gap-4 px-(--space-4)">
        <Link
          to="/"
          aria-label={t("appShell.wordmarkLink")}
          className="text-(length:--text-xl) font-semibold text-(--color-fg-strong) tracking-tight"
        >
          {t("app.name")}
        </Link>
        <RunStateBadge />
        <LiveStatsStrip />
        <div className="ml-auto flex items-center gap-3">
          <RecentEventsMenu />
          <ThemeToggle />
          <AccountMenu />
        </div>
      </div>
    </header>
  );
}

function AccountMenu(): ReactNode {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const detailsRef = useRef<HTMLDetailsElement | null>(null);

  const logoutMutation = useMutation({
    // Review M-1: logout handles its own routing; opt out of the bus
    // so a 401 (e.g., session already expired server-side) doesn't
    // race onSettled's redirect.
    meta: { silenceGlobalErrors: true },
    mutationFn: () =>
      apiFetch<void>("auth/logout", { method: "POST", silenceGlobalErrors: true }),
    onSettled: () => {
      queryClient.setQueryData(["auth", "session"], null);
      queryClient.removeQueries({ queryKey: ["auth", "session"] });
      detailsRef.current?.removeAttribute("open");
      setOpen(false);
      void navigate("/login", { replace: true });
    },
  });

  // Close the popover on outside click — minimal v1 implementation.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent): void => {
      if (detailsRef.current === null) return;
      const target = e.target;
      if (!(target instanceof Node)) return;
      if (!detailsRef.current.contains(target)) {
        detailsRef.current.removeAttribute("open");
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <details
      ref={detailsRef}
      className="relative"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary
        className={cn(
          "inline-flex h-9 cursor-pointer items-center gap-2 rounded-full border border-(--color-border-subtle)",
          "bg-(--color-bg-elevated) px-3 text-(length:--text-sm) font-medium",
          "hover:bg-(--color-bg-sunken) list-none",
        )}
        aria-label={t("appShell.accountMenuTrigger")}
      >
        <UserCircle className="h-5 w-5 text-(--color-fg-muted)" weight="regular" aria-hidden="true" />
        <CaretDown className="h-3 w-3 text-(--color-fg-muted)" weight="bold" aria-hidden="true" />
      </summary>
      {/* The wrapping <details> provides native disclosure semantics;
          adding role="menu" without role="menuitem" children would
          violate ARIA ownership (review H-1). */}
      <div
        className={cn(
          "absolute right-0 mt-2 w-56 rounded-(--radius-md) border border-(--color-border-subtle)",
          "bg-(--color-bg-base) shadow-(--shadow-md)",
        )}
      >
        <Link
          to="/settings/security"
          className="block w-full px-4 py-2 text-left text-(length:--text-sm) text-(--color-fg-default) hover:bg-(--color-bg-elevated)"
        >
          {t("appShell.accountChangePassword")}
        </Link>
        <Link
          to="/settings/security"
          className="block w-full px-4 py-2 text-left text-(length:--text-sm) text-(--color-fg-default) hover:bg-(--color-bg-elevated)"
        >
          {t("appShell.accountApiTokens")}
        </Link>
        <div className="border-t border-(--color-border-subtle)" />
        <button
          type="button"
          onClick={() => {
            logoutMutation.mutate();
          }}
          disabled={logoutMutation.isPending}
          className="block w-full px-4 py-2 text-left text-(length:--text-sm) text-(--color-fg-default) hover:bg-(--color-bg-elevated)"
        >
          {t("common.signOut")}
        </button>
      </div>
    </details>
  );
}

function Footer(): ReactNode {
  return (
    <footer className="border-t border-(--color-border-subtle) bg-(--color-bg-base) py-(--space-3)">
      <div className="mx-auto flex w-full max-w-[1200px] items-center justify-between px-(--space-4) text-(length:--text-xs) text-(--color-fg-muted)">
        <span>{t("app.name")}</span>
        <span>{t("appShell.buildSha", { sha: BUILD_SHA })}</span>
      </div>
    </footer>
  );
}
