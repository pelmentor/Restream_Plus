import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { CircleNotch, WarningCircle } from "@phosphor-icons/react";

import { useRunState } from "@/hooks/useRunState";
import { cn } from "@/lib/cn";
import { t } from "@/messages";

import type { RunStateT } from "@/lib/schemas/run";

/**
 * Replaces Phase 7's <RunStateBadgeStub>. Owns the single
 * `aria-live="polite"` region for run-state announcements
 * (F# UX-E.double-live).
 *
 * Per phase-8-design-memo §H: the wrapping role + live region from the
 * stub is preserved IN PLACE; only the body becomes dynamic. Clicking
 * the badge scrolls to (or navigates to) the Dashboard hero per
 * F# UX-A.badge-from-settings.
 */
export function RunStateBadge(): ReactNode {
  const { runState, runStartedAt } = useRunState();
  const navigate = useNavigate();
  const location = useLocation();
  const timer = useLiveTimer(runStartedAt, runState === "live");

  const visual = visualFor(runState, timer);

  function onClick(): void {
    if (location.pathname === "/") {
      const hero = document.getElementById("dashboard-hero");
      if (hero !== null) {
        hero.scrollIntoView({ behavior: "smooth", block: "start" });
        const heroButton = hero.querySelector<HTMLButtonElement>("[data-hero-button]");
        heroButton?.focus();
      }
      return;
    }
    void navigate("/");
  }

  // Reviewer H-1: `aria-live` on an interactive `<button>` is invalid
  // ARIA (1.2 §6.2.14) — screen readers ignore live-region announcements
  // when focus is on the element. Keep the live region on a wrapping
  // `<div role="status">`, with the activator button inside (matches the
  // Phase 7 stub contract: "the wrapping live region stays").
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-label={visual.ariaLabel}
    >
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "inline-flex items-center gap-2 rounded-full px-3 py-1",
          "transition-colors",
          visual.pillClass,
        )}
      >
        {visual.icon}
        <span
          className={cn(
            "text-(length:--text-2xs) font-semibold uppercase tracking-wider",
            visual.textClass,
          )}
        >
          {visual.label}
        </span>
      </button>
    </div>
  );
}

interface VisualState {
  readonly label: string;
  readonly ariaLabel: string;
  readonly pillClass: string;
  readonly textClass: string;
  readonly icon: ReactNode;
}

function visualFor(runState: RunStateT, timer: string | null): VisualState {
  switch (runState) {
    case "offline":
      return {
        label: t("runState.offline"),
        ariaLabel: t("runState.offlineAria"),
        pillClass: "bg-(--color-bg-elevated)",
        textClass: "text-(--color-fg-muted)",
        icon: <Dot color="muted" />,
      };
    case "starting":
      return {
        label: t("runState.starting"),
        ariaLabel: t("runState.startingAria"),
        pillClass: "bg-(--color-accent-faint)",
        textClass: "text-(--color-accent)",
        icon: (
          <CircleNotch
            className="h-3 w-3 animate-spin text-(--color-accent)"
            weight="bold"
            aria-hidden="true"
          />
        ),
      };
    case "stopping":
      return {
        label: t("runState.stopping"),
        ariaLabel: t("runState.stoppingAria"),
        pillClass: "bg-(--color-error-faint)",
        textClass: "text-(--color-error)",
        icon: (
          <CircleNotch
            className="h-3 w-3 animate-spin text-(--color-error)"
            weight="bold"
            aria-hidden="true"
          />
        ),
      };
    case "armed":
      return {
        label: t("runState.armed"),
        ariaLabel: t("runState.armedAria"),
        pillClass: "bg-(--color-warn-faint)",
        textClass: "text-(--color-warn)",
        icon: <Dot color="warn" />,
      };
    case "live":
      return {
        label: timer === null ? t("runState.live") : `${t("runState.live")} ${timer}`,
        ariaLabel:
          timer === null
            ? t("runState.liveAria")
            : t("runState.liveAriaWithTimer", { timer }),
        pillClass: "bg-(--color-live-faint)",
        textClass: "text-(--color-live) font-(family-name:--font-mono) tabular-nums",
        icon: <Dot color="live" pulse />,
      };
    case "error":
      return {
        label: t("runState.error"),
        ariaLabel: t("runState.errorAria"),
        pillClass: "bg-(--color-error-faint) border border-(--color-error)",
        textClass: "text-(--color-error)",
        icon: (
          <WarningCircle
            className="h-3 w-3 text-(--color-error)"
            weight="fill"
            aria-hidden="true"
          />
        ),
      };
  }
}

function Dot({
  color,
  pulse = false,
}: {
  readonly color: "muted" | "warn" | "live";
  readonly pulse?: boolean;
}): ReactNode {
  const bg =
    color === "muted"
      ? "bg-(--color-fg-muted)"
      : color === "warn"
        ? "bg-(--color-warn)"
        : "bg-(--color-live)";
  return (
    <span
      className={cn("h-2 w-2 rounded-full", bg, pulse && "badge-dot-live")}
      aria-hidden="true"
    />
  );
}

function useLiveTimer(startedAt: Date | null, enabled: boolean): string | null {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled || startedAt === null) return;
    const id = window.setInterval(() => setTick((v) => v + 1), 1_000);
    return () => window.clearInterval(id);
  }, [enabled, startedAt]);
  if (!enabled || startedAt === null) return null;
  // touch tick so eslint sees the dep
  void tick;
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt.getTime()) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function pad(n: number): string {
  return n < 10 ? `0${String(n)}` : String(n);
}
