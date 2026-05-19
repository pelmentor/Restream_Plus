import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { X } from "@phosphor-icons/react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "./Button";
import { ErrorBoundary } from "./ErrorBoundary";
import { LogViewer } from "./LogViewer";
import { MetricGrid, type Metric } from "./MetricGrid";
import { Sparkline } from "./Sparkline";
import { ApiError, apiFetch } from "@/lib/api";
import { cn } from "@/lib/cn";
import { TARGETS_QUERY_KEY } from "@/hooks/useTargets";
import { useLiveMetrics } from "@/hooks/useLiveMetrics";
import {
  REPROMPT_BUSY,
  REPROMPT_CANCELLED,
  useAuthReprompt,
} from "@/hooks/useAuthReprompt";
import { t } from "@/messages";

import type { TargetWithSnapshot } from "@/hooks/useTargets";

export interface TargetDetailsProps {
  readonly target: TargetWithSnapshot | null;
  readonly onClose: () => void;
  /**
   * Reviewer M-4: the activating tile's button. On close, focus
   * returns here. Without this, Radix's `modal={false}` mode drops
   * focus on document.body (no Dialog.Trigger reference to fall back
   * on), violating F# UX-F.focus-slideout.
   */
  readonly triggerRef: RefObject<HTMLButtonElement | null>;
}

/**
 * Right-anchored slide-out (design-system §6.4) with
 * `modal={false}` — Dashboard stays interactive. Focus management per
 * F# UX-F.focus-slideout: on open, focus moves to close button; on
 * close, focus returns to the activating tile (Radix handles via
 * `onCloseAutoFocus` with the activator ref carried in the parent).
 */
export function TargetDetails({ target, onClose, triggerRef }: TargetDetailsProps): ReactNode {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const queryClient = useQueryClient();
  const { egressByTarget } = useLiveMetrics();
  const targetSamples =
    target !== null ? (egressByTarget.get(target.id) ?? []) : [];

  const reprompt = useAuthReprompt();

  // Hex Audit BA-F13 (slice 10): "Retry now" is reprompt-protected so a
  // stolen-cookie attacker cannot defeat the circuit breaker's noisy-
  // neighbor protection (ADR-0003 §Reconnect policy) by spamming the
  // reset endpoint and earning the operator an IP-ban from the target
  // platform.
  const resetMutation = useMutation({
    mutationFn: async (targetId: string) => {
      const grantId = await reprompt("reset_target_worker");
      return apiFetch<void>(`targets/${targetId}/reset-worker?role=primary`, {
        method: "POST",
        headers: { "X-Reprompt-Grant": grantId },
      });
    },
  });

  const handleReset = (targetId: string): void => {
    resetMutation.mutate(targetId, {
      onError: (err: unknown) => {
        if (
          err instanceof ApiError &&
          (err.message === REPROMPT_CANCELLED || err.message === REPROMPT_BUSY)
        ) {
          // Operator dismissed the dialog or a concurrent reprompt was in
          // flight; silent — the action simply does not proceed. Network
          // / auth failures still surface to the mutation's error state.
          // Reviewer IMP-2: `instanceof ApiError` aligns with the six
          // other reprompt call sites (SecurityTab, GeneralTab, VKTab,
          // CustomTab, PersistentTargetTab). A plain `Error` whose
          // `.message` happens to collide with the sentinel strings
          // would otherwise be silently swallowed here.
        }
      },
    });
  };

  const disableMutation = useMutation({
    mutationFn: (targetId: string) =>
      apiFetch<void>(`targets/${targetId}`, {
        method: "PATCH",
        json: { enabled: false },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: TARGETS_QUERY_KEY });
      onClose();
    },
  });

  useEffect(() => {
    if (target !== null) {
      // Focus close button on open. Radix's default lands on the
      // dialog content; override via setTimeout to win the race.
      const handle = window.setTimeout(() => closeRef.current?.focus(), 0);
      return () => window.clearTimeout(handle);
    }
  }, [target]);

  const open = target !== null;

  return (
    <Dialog.Root
      open={open}
      modal={false}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Content
          id="target-details-slideout"
          onOpenAutoFocus={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => {
            // Reviewer M-4: with modal={false} Radix has no Dialog.Trigger
            // to focus on close; thread the activator ref ourselves so
            // keyboard users land back on the tile (F# UX-F.focus-slideout).
            e.preventDefault();
            triggerRef.current?.focus();
          }}
          className={cn(
            // Slice-6 FH2-M2: arbitrary `w-[480px]` tokenized to
            // `--width-slideout`.
            "fixed inset-y-0 right-0 w-(--width-slideout) max-w-full",
            "bg-(--color-bg-base) border-l border-(--color-border-subtle)",
            "shadow-(--shadow-lg) flex flex-col",
            "motion-reduce:transition-opacity",
            "max-sm:w-full max-sm:inset-0 max-sm:border-l-0",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
          )}
        >
          <div className="flex items-center justify-between h-(--space-10) px-(--space-4) border-b border-(--color-border-subtle) shrink-0">
            <Dialog.Title className="text-(length:--text-lg) font-semibold text-(--color-fg-strong)">
              {target?.label ?? ""}
            </Dialog.Title>
            <Dialog.Description className="sr-only">
              {t("targetDetails.description")}
            </Dialog.Description>
            {/* Slice-6 UI-F4: close button grows to size=lg (44×44) for
                WCAG 2.5.5 touch-target floor. `rounded-full` is kept —
                close-button universal affordance. Glyph stays at 20px;
                the hit area expands, the visual mass does not. */}
            <Button
              ref={closeRef}
              iconOnly
              variant="ghost"
              size="lg"
              aria-label={t("targetDetails.close")}
              onClick={onClose}
              className="rounded-full"
            >
              <X className="h-5 w-5" weight="regular" aria-hidden="true" />
            </Button>
          </div>
          {target !== null && (
            <div className="flex-1 overflow-y-auto px-(--space-4) py-(--space-4) flex flex-col gap-(--space-6)">
              <ErrorBoundary
                fallback={
                  <div className="text-(--color-fg-muted) text-(length:--text-sm)">
                    {t("targetDetails.chartUnavailable")}
                  </div>
                }
              >
                {targetSamples.length === 0 ? (
                  <Sparkline
                    samples={targetSamples}
                    ariaLabel={t("targetDetails.sparklineAria")}
                    srSummary={t("targetDetails.sparklineEmptyAria")}
                  />
                ) : (
                  <Sparkline
                    samples={targetSamples}
                    ariaLabel={t("targetDetails.sparklineAria")}
                  />
                )}
              </ErrorBoundary>
              <MetricGrid metrics={detailMetricsFor(target)} columns={4} />
              {target.snapshot?.snapshots_by_role[0]?.last_error !== undefined &&
                target.snapshot.snapshots_by_role[0].last_error !== null && (
                  <div
                    className="rounded-(--radius-md) bg-(--color-error-faint) border-l-4 border-(--color-error) p-(--space-3) text-(length:--text-sm) text-(--color-fg-strong)"
                    role="alert"
                  >
                    {target.snapshot.snapshots_by_role[0].last_error}
                  </div>
                )}
              <LogViewer title={t("targetDetails.logsTitle")} lines={[]} />
              <div className="flex flex-wrap gap-(--space-3)">
                <Button
                  variant="secondary"
                  size="md"
                  onClick={() => handleReset(target.id)}
                  loading={resetMutation.isPending}
                >
                  {t("targetDetails.retryNow")}
                </Button>
                <Button
                  variant="ghost"
                  size="md"
                  onClick={() => disableMutation.mutate(target.id)}
                  loading={disableMutation.isPending}
                >
                  {t("targetDetails.disable")}
                </Button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function detailMetricsFor(target: TargetWithSnapshot): readonly Metric[] {
  const primary = target.snapshot?.snapshots_by_role[0] ?? null;
  if (primary === null) {
    return [
      { label: t("tile.metricBitrate"), value: "—" },
      { label: t("tile.metricDrops"), value: "—" },
      { label: t("tile.metricWorker"), value: "—" },
      { label: t("tile.metricBreaker"), value: "—" },
    ];
  }
  const progress = primary.last_progress;
  const bitrateMetric: Metric =
    progress != null
      ? {
          label: t("tile.metricBitrate"),
          value: (progress.bitrate_kbps / 1000).toFixed(1),
          unit: "Mbps",
        }
      : { label: t("tile.metricBitrate"), value: "—" };
  return [
    bitrateMetric,
    {
      label: t("tile.metricDrops"),
      value: progress != null ? String(progress.drop_frames) : "—",
    },
    { label: t("tile.metricWorker"), value: primary.state.toUpperCase() },
    { label: t("tile.metricBreaker"), value: String(primary.breaker_failures_in_window) },
  ];
}
