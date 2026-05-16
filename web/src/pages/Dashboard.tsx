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

  const totalDrops = useMemo(
    () =>
      enabledTargets.reduce(
        (acc, tt) =>
          acc +
          (tt.snapshot?.snapshots_by_role.reduce(
            (a, w) => a + w.breaker_failures_in_window,
            0,
          ) ?? 0),
        0,
      ),
    [enabledTargets],
  );

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
    return (
      <>
        <ReconnectingBanner />
        <EmptyState
          icon={Broadcast}
          title={t("dashboard.emptyTitle")}
          description={t("dashboard.emptyBody")}
          action={
            <Link
              to="/settings/targets/twitch"
              className="inline-flex h-9 items-center rounded-md bg-(--color-accent) px-(--space-4) text-(length:--text-sm) font-medium text-white hover:bg-(--color-accent-strong)"
            >
              {t("dashboard.emptyCta")}
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
          aggregateBitrate={null}
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
