import type { ReactNode } from "react";

import { useLiveMetrics } from "@/hooks/useLiveMetrics";
import { useRunState } from "@/hooks/useRunState";
import { cn } from "@/lib/cn";
import { t } from "@/messages";

/**
 * Always-visible header chips: aggregate outbound Mbps + host CPU%.
 *
 * Hidden entirely when `run_state === "offline"` — there is nothing
 * to measure, and a greyed "—" would lie about "we tried to measure
 * but got zero." Reveal on starting / armed / live / stopping; hide
 * again on return to offline.
 *
 * CPU threshold bands (`text-(--color-warn|error)` on the value):
 *   < 70%  default
 *   70-90% warn
 *   > 90%  error
 *
 * Egress doesn't have absolute thresholds — what's "good" depends on
 * the operator's OBS bitrate config — so the chip just shows the number.
 * Per-target color states live on the TargetTile / Sparkline.
 *
 * Slice-6 UX-F7 (responsive):
 *   - `>= md` (≥768px) — inline with header chips (the v1 layout).
 *   - `sm…md` (640–768px) — renders below the header in a thin
 *     secondary bar (`h-(--size-control-sm)` 28px), full-width, center-
 *     justified. Drops out of the header chip row to free space.
 *   - `< sm` (<640px) — collapses to a single CPU chip with both
 *     signals exposed in an `aria-label`. CPU is the operationally
 *     critical signal on phone; egress is reachable via aria-label.
 */
export function LiveStatsStrip(): ReactNode {
  const { runState } = useRunState();
  const { hostStats, aggregateBuffer } = useLiveMetrics();

  if (runState === "offline") return null;

  const latestEgress = aggregateBuffer[aggregateBuffer.length - 1];
  const egressMbps = latestEgress !== undefined ? (latestEgress.bitrate / 1000).toFixed(1) : "—";

  const cpuPct = hostStats?.cpu_total_pct ?? null;
  const cpuText = cpuPct === null ? "—" : Math.round(cpuPct).toString();
  const cpuClass = cpuClassFor(cpuPct);

  const fullChips = (
    <>
      <Chip
        label={t("liveStats.egress")}
        value={egressMbps}
        unit={t("liveStats.unitMbps")}
        valueClass="text-(--color-fg-strong)"
      />
      <Chip
        label={t("liveStats.cpu")}
        value={cpuText}
        unit={t("liveStats.unitPct")}
        valueClass={cpuClass}
      />
    </>
  );

  return (
    <>
      {/* >= md: inline header chip row (the existing slot) */}
      <div
        className="hidden md:flex items-center gap-(--space-3) text-(length:--text-xs)"
        data-testid="live-stats-strip"
      >
        {fullChips}
      </div>
      {/* sm…md: secondary thin bar below the header — full width */}
      <div
        className={cn(
          "hidden sm:flex md:hidden",
          "fixed top-16 inset-x-0 z-10 h-(--size-control-sm)",
          "items-center justify-center gap-(--space-3) text-(length:--text-xs)",
          "border-b border-(--color-border-subtle) bg-(--color-bg-base)/95 backdrop-blur",
        )}
        data-testid="live-stats-strip-secondary"
      >
        {fullChips}
      </div>
      {/* < sm: compact CPU-only chip with both signals in aria-label */}
      <div
        className="flex sm:hidden text-(length:--text-xs)"
        data-testid="live-stats-strip-compact"
      >
        <Chip
          label={t("liveStats.cpu")}
          value={cpuText}
          unit={t("liveStats.unitPct")}
          valueClass={cpuClass}
          ariaLabel={t("liveStats.compactAria", { cpu: cpuText, egress: egressMbps })}
        />
      </div>
    </>
  );
}

interface ChipProps {
  readonly label: string;
  readonly value: string;
  readonly unit: string;
  readonly valueClass: string;
  readonly ariaLabel?: string;
}

function Chip({ label, value, unit, valueClass, ariaLabel }: ChipProps): ReactNode {
  return (
    <span
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-baseline gap-1 rounded-(--radius-sm) bg-(--color-bg-sunken)",
        "px-(--space-2) py-(--space-px)",
      )}
    >
      <span className="text-(length:--text-2xs) uppercase tracking-wider text-(--color-fg-muted)">
        {label}
      </span>
      <span
        className={cn(
          "font-(family-name:--font-mono) tabular-nums",
          valueClass,
        )}
      >
        {value}
      </span>
      <span className="text-(--color-fg-muted)">{unit}</span>
    </span>
  );
}

function cpuClassFor(cpuPct: number | null): string {
  if (cpuPct === null) return "text-(--color-fg-muted)";
  if (cpuPct > 90) return "text-(--color-error)";
  if (cpuPct > 70) return "text-(--color-warn)";
  return "text-(--color-fg-strong)";
}
