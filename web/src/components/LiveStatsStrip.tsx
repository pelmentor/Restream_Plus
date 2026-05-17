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

  return (
    <div
      className="hidden md:flex items-center gap-(--space-3) text-(length:--text-xs)"
      data-testid="live-stats-strip"
    >
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
    </div>
  );
}

interface ChipProps {
  readonly label: string;
  readonly value: string;
  readonly unit: string;
  readonly valueClass: string;
}

function Chip({ label, value, unit, valueClass }: ChipProps): ReactNode {
  return (
    <span
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
