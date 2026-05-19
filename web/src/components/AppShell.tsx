import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CaretDown, UserCircle } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

import { Button } from "@/components/Button";
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
          className="flex-1 mx-auto w-full max-w-(--width-app) px-(--space-4) py-(--space-6) outline-none"
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
      <div className="mx-auto flex h-full w-full max-w-(--width-app) items-center gap-(--space-4) px-(--space-4)">
        <Link
          to="/"
          aria-label={t("appShell.wordmarkLink")}
          className="text-(length:--text-xl) font-semibold text-(--color-fg-strong) tracking-tight"
        >
          {t("app.name")}
        </Link>
        <RunStateBadge />
        <LiveStatsStrip />
        <div className="ml-auto flex items-center gap-(--space-3)">
          <RecentEventsMenu />
          <ThemeToggle />
          <AccountMenu />
        </div>
      </div>
    </header>
  );
}

/**
 * Slice-6 UX-F3: replaced `<details>/<summary>` (no role="menu" /
 * role="menuitem" + no arrow-key navigation) with Radix DropdownMenu
 * which provides the full APG menu pattern out of the box. Trigger
 * uses `Button variant="secondary" size="lg"` per UX-architect memo §4
 * + UI-F4 touch-target floor. Sign-out's `onSelect` preventDefault
 * keeps the menu open until the mutation's `onSettled` navigates,
 * dodging the focus-return race with Radix's auto-close behavior.
 */
function AccountMenu(): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Route-change close: clicking a menu link with `asChild` doesn't
  // auto-close Radix; close on every pathname transition.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  const logoutMutation = useMutation({
    meta: { silenceGlobalErrors: true },
    mutationFn: () =>
      apiFetch<void>("auth/logout", { method: "POST", silenceGlobalErrors: true }),
    onSettled: () => {
      queryClient.setQueryData(["auth", "session"], null);
      queryClient.removeQueries({ queryKey: ["auth", "session"] });
      setOpen(false);
      void navigate("/login", { replace: true });
    },
  });

  const itemClass = cn(
    "block w-full px-(--space-4) py-(--space-2) text-left text-(length:--text-sm)",
    "text-(--color-fg-default) outline-none",
    "data-[highlighted]:bg-(--color-bg-elevated)",
    "data-[disabled]:text-(--color-fg-disabled) data-[disabled]:cursor-not-allowed",
  );

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        {/* Slice-6 reviewer H-1: marked `iconOnly` so the Button takes
            its square 44×44 geometry, drops the spurious spinner gutter
            (gutter renders even when `loading` is never set on a Radix
            trigger — wasted 14px). UserCircle + CaretDown pair fits the
            chip-icon role; aesthetic chip preserved via `rounded-full`
            + intra-icon `gap-(--space-1)`. */}
        <Button
          iconOnly
          variant="secondary"
          size="lg"
          aria-label={t("appShell.accountMenuTrigger")}
          className="gap-(--space-1) rounded-full"
        >
          <UserCircle className="h-5 w-5 text-(--color-fg-muted)" weight="regular" aria-hidden="true" />
          <CaretDown className="h-3 w-3 text-(--color-fg-muted)" weight="bold" aria-hidden="true" />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className={cn(
            "z-50 w-(--width-popover) rounded-(--radius-md) border bg-(--color-bg-base)",
            "border-(--color-border-subtle) shadow-(--shadow-popover) py-(--space-1)",
          )}
        >
          <DropdownMenu.Item asChild>
            <Link to="/settings/security" className={itemClass}>
              {t("appShell.accountChangePassword")}
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Item asChild>
            <Link to="/settings/security" className={itemClass}>
              {t("appShell.accountApiTokens")}
            </Link>
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="my-(--space-1) h-px bg-(--color-border-subtle)" />
          <DropdownMenu.Item
            // Slice-6 UX-F3: preventDefault keeps the menu open until
            // logout mutation's `onSettled` fires — without it Radix
            // closes + returns focus to trigger, racing our `navigate`
            // and occasionally landing focus on an unmounted node.
            onSelect={(e) => {
              e.preventDefault();
              logoutMutation.mutate();
            }}
            disabled={logoutMutation.isPending}
            className={itemClass}
          >
            {t("common.signOut")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function Footer(): ReactNode {
  return (
    <footer className="border-t border-(--color-border-subtle) bg-(--color-bg-base) py-(--space-3)">
      <div className="mx-auto flex w-full max-w-(--width-app) items-center justify-between px-(--space-4) text-(length:--text-xs) text-(--color-fg-muted)">
        <span>{t("app.name")}</span>
        <span>{t("appShell.buildSha", { sha: BUILD_SHA })}</span>
      </div>
    </footer>
  );
}
