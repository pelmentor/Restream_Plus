import { useMemo, useRef, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Broadcast } from "@phosphor-icons/react";

import { EmptyState } from "@/components/EmptyState";
import { HeroCard } from "@/components/HeroCard";
import { ReconnectingBanner } from "@/components/ReconnectingBanner";
import { Skeleton } from "@/components/Skeleton";
import { TargetDetails } from "@/components/TargetDetails";
import { TargetTile } from "@/components/TargetTile";
import { apiFetch } from "@/lib/api";
import { SettingsView, type SettingsViewT } from "@/lib/schemas/settings";
import { useLiveMetrics } from "@/hooks/useLiveMetrics";
import { useRunState } from "@/hooks/useRunState";
import { useTargets } from "@/hooks/useTargets";
import { t } from "@/messages";

/**
 * The Dashboard. Composes the hero, the tile grid, the slide-out, the
 * reconnecting banner. Per phase-8-design-memo §I/L/T.
 *
 * URL state: `?target=<id>` opens the matching tile's details
 * slide-out (UX Q11 / SA Q8). Unknown id silently closes.
 */
export function Dashboard(): ReactNode {
  const { runState, isPending: runPending } = useRunState();
  const { targets, isPending: targetsPending } = useTargets();
  const liveMetrics = useLiveMetrics();

  const { data: settings, isPending: settingsPending } = useQuery<SettingsViewT>({
    queryKey: ["settings"],
    queryFn: () => apiFetch("settings", {}, SettingsView),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const [searchParams, setSearchParams] = useSearchParams();
  const openTargetId = searchParams.get("target");
  const openTarget =
    openTargetId === null ? null : (targets.find((tt) => tt.id === openTargetId) ?? null);

  // Reviewer M-4: the most-recently-opened tile button — focus returns
  // here when the slide-out closes (F# UX-F.focus-slideout).
  const activatorRef = useRef<HTMLButtonElement | null>(null);

  const enabledTargets = useMemo(
    () => targets.filter((tt) => tt.enabled),
    [targets],
  );

  const runningCount = useMemo(
    () => enabledTargets.filter((tt) => tt.snapshot?.ui_state === "running").length,
    [enabledTargets],
  );

  // totalDrops: sum of ffmpeg drop_frames across enabled targets'
  // primary worker. This is the operator-honest "drops" — the number
  // ffmpeg actually couldn't push. Falls back to 0 when no progress
  // frame has arrived yet for a target.
  const totalDrops = useMemo(
    () =>
      enabledTargets.reduce((acc, tt) => {
        const primary = tt.snapshot?.snapshots_by_role.find((s) => s.role === "primary");
        return acc + (primary?.last_progress?.drop_frames ?? 0);
      }, 0),
    [enabledTargets],
  );

  // Latest aggregate Mbps from the rolling buffer (sum of per-target
  // primaries' last_progress.bitrate_kbps). Null when no samples yet.
  const aggregateBitrate = useMemo(() => {
    const latest = liveMetrics.aggregateBuffer[liveMetrics.aggregateBuffer.length - 1];
    return latest === undefined ? null : latest.bitrate / 1000;
  }, [liveMetrics.aggregateBuffer]);

  if (runPending || targetsPending || settingsPending) {
    return (
      <>
        <ReconnectingBanner />
        <div className="flex flex-col gap-(--space-6)">
          <Skeleton.Tile />
          <div className="grid gap-(--space-4) grid-cols-[repeat(auto-fit,minmax(240px,1fr))]">
            <Skeleton.Tile />
            <Skeleton.Tile />
            <Skeleton.Tile />
            <Skeleton.Tile />
          </div>
        </div>
      </>
    );
  }

  if (enabledTargets.length === 0) {
    // Two distinct empty states: never-configured vs all-disabled.
    // The original copy ("Add a target") was wrong for the latter
    // case — the user already added them, they just need to enable
    // one. Conflating the two left operators stuck staring at a CTA
    // that told them to do something they'd already done.
    const noTargetsAtAll = targets.length === 0;
    const title = noTargetsAtAll
      ? t("dashboard.emptyTitle")
      : t("dashboard.disabledTitle");
    const body = noTargetsAtAll
      ? t("dashboard.emptyBody")
      : t("dashboard.disabledBody");
    const cta = noTargetsAtAll
      ? t("dashboard.emptyCta")
      : t("dashboard.disabledCta");
    // Hex Audit CR-F10 (slice 10): link target dynamically derived from
    // the operator's actual targets in the "all disabled" case (jump
    // straight to the type they have configured), defaults to the
    // settings index in the "never configured" case (lets them pick
    // a platform). Pre-slice-10 hardcoded `/twitch` even if the
    // operator had only a YouTube target — a tap-and-stare moment
    // that surfaced as the audit finding.
    const ctaLink = noTargetsAtAll
      ? "/settings/targets"
      : `/settings/targets/${targets[0]?.type ?? "twitch"}`;
    return (
      <>
        <ReconnectingBanner />
        <EmptyState
          icon={Broadcast}
          title={title}
          description={body}
          action={
            <Link
              to={ctaLink}
              className="inline-flex h-(--size-control-md) items-center rounded-md bg-(--color-accent) px-(--space-4) text-(length:--text-sm) font-medium text-(--color-on-accent) hover:bg-(--color-accent-strong)"
            >
              {cta}
            </Link>
          }
        />
      </>
    );
  }

  return (
    <>
      <ReconnectingBanner />
      <div className="flex flex-col gap-(--space-8)">
        <HeroCard
          runState={runState}
          enabledTargets={enabledTargets}
          firstRunComplete={settings?.first_run_complete ?? true}
          ingestKeyLast4={settings?.ingest_key_last4 ?? null}
          aggregateBitrate={aggregateBitrate}
          aggregateSamples={liveMetrics.aggregateBuffer}
          ingestKbps={liveMetrics.hostStats?.ingest_kbps ?? null}
          runningCount={runningCount}
          totalEnabled={enabledTargets.length}
          totalDrops={totalDrops}
        />

        <section aria-labelledby="targets-heading" className="flex flex-col gap-(--space-4)">
          <h2
            id="targets-heading"
            className="text-(length:--text-lg) font-semibold text-(--color-fg-strong)"
          >
            {t("dashboard.targetsHeading")}
          </h2>
          <div className="grid gap-(--space-4) grid-cols-[repeat(auto-fit,minmax(240px,1fr))]">
            {targets.map((tt) => (
              <TargetTile
                key={tt.id}
                target={tt}
                isOpen={openTargetId === tt.id}
                onOpen={(button) => {
                  activatorRef.current = button;
                  setSearchParams({ target: tt.id });
                }}
              />
            ))}
          </div>
        </section>
      </div>

      <TargetDetails
        target={openTarget}
        triggerRef={activatorRef}
        onClose={() => {
          const next = new URLSearchParams(searchParams);
          next.delete("target");
          setSearchParams(next, { replace: true });
        }}
      />
    </>
  );
}
