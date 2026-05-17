import type { ReactNode } from "react";
import {
  ArrowsClockwise,
  Broadcast,
  Circle,
  CircleNotch,
  Prohibit,
  Warning,
  WarningCircle,
  XCircle,
  type Icon,
} from "@phosphor-icons/react";

import { MetricGrid, type Metric } from "./MetricGrid";
import { cn } from "@/lib/cn";
import { t } from "@/messages";

import type { TargetWithSnapshot } from "@/hooks/useTargets";
import type { TargetUiStateT } from "@/lib/schemas/run";

export interface TargetTileProps {
  readonly target: TargetWithSnapshot;
  readonly isOpen: boolean;
  readonly onOpen: (button: HTMLButtonElement) => void;
}

/**
 * Design-system §6.3 + phase-8-design-memo §L. Whole tile is one
 * keyboard button (Enter / Space → open slide-out). No nested
 * interactive elements.
 *
 * Status icons are Phosphor only (F# UI-E.theme-svg extension).
 *
 * Reviewer M-4: passes its `currentTarget` to `onOpen` so the
 * Dashboard can capture an activator ref for focus-return on close.
 */
export function TargetTile({ target, isOpen, onOpen }: TargetTileProps): ReactNode {
  const uiState = target.snapshot?.ui_state ?? (target.enabled ? "idle" : "disabled");
  const visual = visualFor(uiState);

  return (
    <button
      type="button"
      onClick={(e) => onOpen(e.currentTarget)}
      aria-expanded={isOpen}
      aria-controls="target-details-slideout"
      className={cn(
        "flex flex-col gap-(--space-3) w-full text-left",
        "rounded-(--radius-lg) bg-(--color-bg-elevated) p-(--space-4)",
        "shadow-(--shadow-xs) transition-[transform,box-shadow,border-color] duration-150",
        "hover:-translate-y-px hover:shadow-(--shadow-sm)",
        visual.borderClass,
        visual.containerOpacityClass,
      )}
    >
      <div className="flex items-center justify-between gap-(--space-3) min-w-0">
        <span className="flex items-center gap-(--space-2) min-w-0">
          <Broadcast
            className="h-5 w-5 shrink-0 text-(--color-fg-muted)"
            weight="regular"
            aria-hidden="true"
          />
          <span className="font-semibold text-(--color-fg-strong) truncate">{target.label}</span>
        </span>
        <StatusPill visual={visual} />
      </div>
      <MetricGrid metrics={metricsFor(target)} />
    </button>
  );
}

function StatusPill({ visual }: { readonly visual: VisualState }): ReactNode {
  const IconComp = visual.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-(--radius-sm) px-(--space-2) py-(--space-px)",
        "text-(length:--text-2xs) font-semibold uppercase tracking-wider",
        visual.pillClass,
      )}
    >
      {IconComp !== null && (
        <IconComp
          className={cn("h-3 w-3", visual.iconSpinClass)}
          weight={visual.iconWeight}
          aria-hidden="true"
        />
      )}
      {visual.label}
    </span>
  );
}

interface VisualState {
  readonly label: string;
  readonly pillClass: string;
  readonly borderClass: string;
  readonly containerOpacityClass: string;
  readonly icon: Icon | null;
  readonly iconWeight: "regular" | "fill" | "bold";
  readonly iconSpinClass: string;
}

function visualFor(state: TargetUiStateT): VisualState {
  switch (state) {
    case "disabled":
      return {
        label: t("tile.statusDisabled"),
        pillClass: "bg-(--color-bg-sunken) text-(--color-fg-muted)",
        borderClass: "border border-(--color-border-subtle)",
        containerOpacityClass: "opacity-60",
        icon: Prohibit,
        iconWeight: "regular",
        iconSpinClass: "",
      };
    case "disabled_misconfigured":
      return {
        label: t("tile.statusNoKey"),
        pillClass: "bg-(--color-warn-faint) text-(--color-warn)",
        borderClass: "border border-(--color-warn)",
        containerOpacityClass: "",
        icon: Warning,
        iconWeight: "fill",
        iconSpinClass: "",
      };
    case "idle":
      return {
        label: t("tile.statusEnabled"),
        pillClass: "bg-(--color-bg-sunken) text-(--color-fg-default)",
        borderClass: "border border-(--color-border-subtle)",
        containerOpacityClass: "",
        icon: Circle,
        iconWeight: "regular",
        iconSpinClass: "",
      };
    case "starting":
      return {
        label: t("tile.statusStarting"),
        pillClass: "bg-(--color-accent-faint) text-(--color-accent)",
        borderClass: "border border-(--color-accent)",
        containerOpacityClass: "",
        icon: CircleNotch,
        iconWeight: "bold",
        iconSpinClass: "animate-spin",
      };
    case "running":
      return {
        label: t("tile.statusLive"),
        pillClass: "bg-(--color-live-faint) text-(--color-live)",
        borderClass: "border border-(--color-live)",
        containerOpacityClass: "",
        icon: Circle,
        iconWeight: "fill",
        iconSpinClass: "",
      };
    case "degraded":
      return {
        label: t("tile.statusDegraded"),
        pillClass: "bg-(--color-warn-faint) text-(--color-warn)",
        borderClass: "border border-(--color-warn)",
        containerOpacityClass: "",
        icon: WarningCircle,
        iconWeight: "fill",
        iconSpinClass: "",
      };
    case "errored":
      return {
        label: t("tile.statusReconnecting"),
        pillClass: "bg-(--color-error-faint) text-(--color-error)",
        borderClass: "border border-(--color-error)",
        containerOpacityClass: "",
        icon: ArrowsClockwise,
        iconWeight: "bold",
        iconSpinClass: "animate-spin",
      };
    case "failed_open":
      return {
        label: t("tile.statusStopped"),
        pillClass: "bg-(--color-error-faint) text-(--color-error)",
        borderClass: "border-2 border-(--color-error)",
        containerOpacityClass: "",
        icon: XCircle,
        iconWeight: "fill",
        iconSpinClass: "",
      };
  }
}

function metricsFor(target: TargetWithSnapshot): readonly Metric[] {
  const snapshot = target.snapshot;
  const dash = "—";
  if (snapshot === null) {
    return [
      { label: t("tile.metricBitrate"), value: dash },
      { label: t("tile.metricDrops"), value: dash },
    ];
  }
  const primary = snapshot.snapshots_by_role.find((s) => s.role === "primary") ?? null;
  if (primary === null) {
    return [
      { label: t("tile.metricBitrate"), value: dash },
      { label: t("tile.metricDrops"), value: dash },
    ];
  }
  // last_progress is the parsed ffmpeg progress frame (Phase-12 stats).
  // Absent → "—" rather than 0, because zero implies the worker is
  // actively pushing 0 Mbps, which would mislead the operator.
  const progress = primary.last_progress;
  if (progress == null) {
    return [
      { label: t("tile.metricBitrate"), value: dash },
      { label: t("tile.metricDrops"), value: dash },
    ];
  }
  return [
    {
      label: t("tile.metricBitrate"),
      value: (progress.bitrate_kbps / 1000).toFixed(1),
      unit: "Mbps",
    },
    { label: t("tile.metricDrops"), value: String(progress.drop_frames) },
  ];
}
