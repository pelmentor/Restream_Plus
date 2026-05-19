import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Banner } from "./Banner";
import { InlinePromptCard } from "./InlinePromptCard";
import { Sparkline, type SparklineSample } from "./Sparkline";
import { apiFetch, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { TARGETS_QUERY_KEY, type TargetWithSnapshot } from "@/hooks/useTargets";
import { t } from "@/messages";

import type { RunStateT } from "@/lib/schemas/run";

const FIRST_RUN_LS_KEY = "restream-plus.firstRunHintDismissed";

export interface HeroCardProps {
  readonly runState: RunStateT;
  readonly enabledTargets: readonly TargetWithSnapshot[];
  readonly firstRunComplete: boolean;
  readonly ingestKeyLast4: string | null;
  /** Latest aggregate egress in Mbps (sum across enabled targets), or
   * null when no progress sample has arrived yet. */
  readonly aggregateBitrate: number | null;
  /** Rolling sparkline buffer (last ~60 s of aggregate egress in kbps).
   * Empty array hides the sparkline. */
  readonly aggregateSamples: readonly SparklineSample[];
  /** Ingest-side kbps from nginx-rtmp, or null when unavailable (no
   * publisher / nginx wedged). UI renders "—", never 0. */
  readonly ingestKbps: number | null;
  readonly runningCount: number;
  readonly totalEnabled: number;
  readonly totalDrops: number;
}

/**
 * ADR-0015 auto-run-on-publish: hero is status-only. OBS publishing is
 * the single trigger — no Start/Stop button. The card shows current
 * run state, ingest push URL, and live stats when LIVE.
 *
 * VK credentials are surfaced inline when missing so the operator can
 * paste them before OBS connects; the saved keys are picked up by the
 * supervisor on the next auto-start. Skip dismisses the prompt; those
 * VK targets will resolve to `disabled_misconfigured` for the next
 * session (existing supervisor `_credentials_satisfied` gate).
 */
export function HeroCard({
  runState,
  enabledTargets,
  firstRunComplete,
  ingestKeyLast4,
  aggregateBitrate,
  aggregateSamples,
  ingestKbps,
  runningCount,
  totalEnabled,
  totalDrops,
}: HeroCardProps): ReactNode {
  const queryClient = useQueryClient();
  const [vkPromptDismissed, setVkPromptDismissed] = useState(false);
  const [hintDismissed, setHintDismissed] = useState(() => readDismissed());

  // Auto-dismiss the first-run hint on first LIVE transition.
  useEffect(() => {
    if (runState === "live" && !hintDismissed) {
      window.localStorage.setItem(FIRST_RUN_LS_KEY, "1");
      setHintDismissed(true);
    }
  }, [runState, hintDismissed]);

  // Reviewer IMP-3: vkPromptDismissed must NOT persist across run
  // boundaries. ADR-0015 auto-run wipes VK per-session credentials on
  // every stop_run; the operator's "Save" from session N leaves
  // vkPromptDismissed=true, and session N+1's wiped creds would then
  // show no prompt. Reset the flag every time we land back in OFFLINE
  // from any non-OFFLINE state.
  const prevStateRef = useRef(runState);
  useEffect(() => {
    if (prevStateRef.current !== "offline" && runState === "offline") {
      setVkPromptDismissed(false);
    }
    prevStateRef.current = runState;
  }, [runState]);

  const vkNeedingKey = enabledTargets.filter(
    (tgt) => tgt.type === "vk_live" && !tgt.has_credential,
  );

  const credentialMutation = useMutation<unknown, ApiError, { id: string; key: string }>({
    mutationFn: ({ id, key }) =>
      apiFetch(`targets/${id}/credential`, {
        method: "PUT",
        json: { stream_key: key },
      }),
  });

  async function handleSaveCredentials(perTarget: ReadonlyMap<string, string>): Promise<void> {
    for (const [id, key] of perTarget) {
      await credentialMutation.mutateAsync({ id, key });
    }
    await queryClient.invalidateQueries({ queryKey: TARGETS_QUERY_KEY });
    setVkPromptDismissed(true);
  }

  function handleSkip(): void {
    setVkPromptDismissed(true);
  }

  const showVkPrompt =
    !vkPromptDismissed && vkNeedingKey.length > 0 && runState === "offline";
  const showFirstRunHint = !firstRunComplete && !hintDismissed && runState === "offline";

  return (
    <section
      id="dashboard-hero"
      className={cn(
        "mx-auto w-full max-w-(--width-hero) rounded-(--radius-xl) bg-(--color-bg-elevated)",
        "border border-(--color-border-subtle) shadow-(--shadow-sm) p-(--space-8)",
        // Reviewer M-3: --color-* tokens ship full `hsl(...)` values per
        // Phase 7 §D — wrapping in hsl() again was invalid CSS and
        // silently hid the live-ambient ring.
        runState === "live" &&
          "[box-shadow:0_0_0_1px_var(--color-live-faint),var(--shadow-sm)]",
      )}
    >
      <div className="flex flex-col items-center gap-(--space-6)">
        <StatePill runState={runState} />
        <HeroBody
          runState={runState}
          ingestKeyLast4={ingestKeyLast4}
          aggregateBitrate={aggregateBitrate}
          aggregateSamples={aggregateSamples}
          ingestKbps={ingestKbps}
          runningCount={runningCount}
          totalEnabled={totalEnabled}
          totalDrops={totalDrops}
        />
        {showFirstRunHint && <FirstRunHint />}
      </div>
      {showVkPrompt && (
        <InlinePromptCard
          vkTargets={vkNeedingKey}
          submitting={credentialMutation.isPending}
          onSave={(perTarget) => {
            void handleSaveCredentials(perTarget);
          }}
          onSkip={handleSkip}
        />
      )}
    </section>
  );
}

interface StatePillProps {
  readonly runState: RunStateT;
}

function StatePill({ runState }: StatePillProps): ReactNode {
  // Non-interactive visual badge — replaces the old Start/Stop button
  // since OBS publishing is the trigger (ADR-0015). Sized to occupy
  // the same visual mass so the hero's vertical rhythm is preserved.
  const tone = pillTone(runState);
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex h-16 min-w-(--width-hero-action-min) max-w-(--width-hero-action-max)",
        "items-center justify-center rounded-md px-(--space-6)",
        "font-semibold text-(length:--text-xl) tracking-wide",
        tone.bg,
        tone.fg,
        runState === "live" && "animate-pulse",
      )}
    >
      {t(pillLabelKey(runState))}
    </div>
  );
}

function pillLabelKey(runState: RunStateT): "hero.stateOffline" | "hero.stateStarting" | "hero.stateArmed" | "hero.stateLive" | "hero.stateStopping" | "hero.stateError" {
  switch (runState) {
    case "offline":
      return "hero.stateOffline";
    case "starting":
      return "hero.stateStarting";
    case "armed":
      return "hero.stateArmed";
    case "live":
      return "hero.stateLive";
    case "stopping":
      return "hero.stateStopping";
    case "error":
      return "hero.stateError";
  }
}

interface PillTone {
  readonly bg: string;
  readonly fg: string;
}

function pillTone(runState: RunStateT): PillTone {
  switch (runState) {
    case "offline":
      return { bg: "bg-(--color-bg-sunken)", fg: "text-(--color-fg-muted)" };
    case "starting":
    case "stopping":
      return { bg: "bg-(--color-info-faint)", fg: "text-(--color-info)" };
    case "armed":
      return { bg: "bg-(--color-warn-faint)", fg: "text-(--color-warn)" };
    case "live":
      return { bg: "bg-(--color-live-faint)", fg: "text-(--color-fg-strong)" };
    case "error":
      return { bg: "bg-(--color-error-faint)", fg: "text-(--color-error)" };
  }
}

interface HeroBodyProps {
  readonly runState: RunStateT;
  readonly ingestKeyLast4: string | null;
  readonly aggregateBitrate: number | null;
  readonly aggregateSamples: readonly SparklineSample[];
  readonly ingestKbps: number | null;
  readonly runningCount: number;
  readonly totalEnabled: number;
  readonly totalDrops: number;
}

function HeroBody({
  runState,
  ingestKeyLast4,
  aggregateBitrate,
  aggregateSamples,
  ingestKbps,
  runningCount,
  totalEnabled,
  totalDrops,
}: HeroBodyProps): ReactNode {
  // RTMP ingest lives on port 1935 (plain RTMP), never on whatever port
  // the panel is served from. `window.location.host` is "host:port" of
  // the HTTP panel — using that verbatim with `rtmps://…/live` produces
  // a URL that's wrong in every deployment shape: prod (nginx-rtmp on
  // :1935) and dev (MediaMTX on :1935). The earlier "rtmps://…/live"
  // copy was a v1.0 bug that nobody noticed because operators have OBS
  // configured externally — the panel's display didn't drive the actual
  // push. Now we tell the truth: rtmp:// + hostname + :1935.
  // TODO when we add a TLS terminator in front of RTMP (rtmps:// support
  // via stunnel / nginx stream module): expose the protocol+port via
  // /api/settings and read it here instead of hardcoding.
  const ingestUrl = `rtmp://${window.location.hostname}:1935/live`;
  switch (runState) {
    case "offline":
      return (
        <div className="w-full mt-(--space-2) rounded-(--radius-lg) bg-(--color-bg-sunken) p-(--space-4) text-center">
          <dl className="grid gap-(--space-3)">
            <div>
              <dt className="text-(length:--text-2xs) uppercase tracking-wider text-(--color-fg-muted)">
                {t("hero.pushOBSTo")}
              </dt>
              <dd className="font-(family-name:--font-mono) text-(length:--text-sm) text-(--color-fg-strong) break-all">
                {ingestUrl}
              </dd>
            </div>
            <div>
              <dt className="text-(length:--text-2xs) uppercase tracking-wider text-(--color-fg-muted)">
                {t("hero.streamKey")}
              </dt>
              <dd className="font-(family-name:--font-mono) text-(length:--text-sm) text-(--color-fg-strong)">
                {ingestKeyLast4 === null ? "—" : `●●●●●●●● ${ingestKeyLast4}`}
                <span className="block text-(length:--text-xs) text-(--color-fg-muted) font-(family-name:--font-sans) mt-1">
                  {t("hero.revealInSettings")}
                </span>
              </dd>
            </div>
          </dl>
        </div>
      );
    case "starting":
      return <p className="text-(--color-fg-muted)">{t("hero.startingBody")}</p>;
    case "armed":
      return (
        <Banner variant="info" title={t("hero.armedTitle")}>
          {t("hero.armedBody")}
        </Banner>
      );
    case "live":
      return (
        <div className="w-full flex flex-col items-center gap-(--space-3)">
          <p className="text-(length:--text-base) text-(--color-fg-default)">
            {t("hero.liveSummary", {
              runningCount,
              totalEnabled,
              bitrate: aggregateBitrate === null ? "—" : aggregateBitrate.toFixed(1),
              drops: totalDrops,
            })}
          </p>
          <IngestEgressPair
            ingestKbps={ingestKbps}
            egressMbps={aggregateBitrate}
          />
          {aggregateSamples.length > 0 && (
            <Sparkline
              samples={aggregateSamples}
              ariaLabel={t("liveStats.sparklineAria")}
            />
          )}
        </div>
      );
    case "stopping":
      return <p className="text-(--color-fg-muted)">{t("hero.stoppingBody")}</p>;
    case "error":
      return (
        <Banner variant="error" title={t("hero.errorTitle")}>
          {t("hero.errorBody")}
        </Banner>
      );
  }
}

interface IngestEgressPairProps {
  readonly ingestKbps: number | null;
  readonly egressMbps: number | null;
}

function IngestEgressPair({ ingestKbps, egressMbps }: IngestEgressPairProps): ReactNode {
  const ingestMbps = ingestKbps === null ? "—" : (ingestKbps / 1000).toFixed(1);
  const egressDisplay = egressMbps === null ? "—" : egressMbps.toFixed(1);
  return (
    <dl className="flex items-baseline gap-(--space-6) text-(length:--text-sm)">
      <div className="flex flex-col items-center gap-1">
        <dt className="text-(length:--text-2xs) uppercase tracking-wider text-(--color-fg-muted)">
          {t("liveStats.ingest")}
        </dt>
        <dd className="font-(family-name:--font-mono) tabular-nums text-(--color-fg-strong)">
          {ingestMbps}
          <span className="ml-1 text-(--color-fg-muted) font-(family-name:--font-sans)">
            {t("liveStats.unitMbps")}
          </span>
        </dd>
      </div>
      <span aria-hidden="true" className="text-(--color-fg-muted)">→</span>
      <div className="flex flex-col items-center gap-1">
        <dt className="text-(length:--text-2xs) uppercase tracking-wider text-(--color-fg-muted)">
          {t("liveStats.egress")}
        </dt>
        <dd className="font-(family-name:--font-mono) tabular-nums text-(--color-fg-strong)">
          {egressDisplay}
          <span className="ml-1 text-(--color-fg-muted) font-(family-name:--font-sans)">
            {t("liveStats.unitMbps")}
          </span>
        </dd>
      </div>
    </dl>
  );
}

function FirstRunHint(): ReactNode {
  return (
    <div className="w-full mt-(--space-2) rounded-(--radius-md) bg-(--color-info-faint) border-l-2 border-(--color-info) p-(--space-4)">
      <ol className="flex flex-col gap-(--space-2)">
        {(["firstRunStep1", "firstRunStep2", "firstRunStep3"] as const).map((key, i) => (
          <li key={key} className="flex items-start gap-(--space-3)">
            <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-(--color-info) text-(--color-on-accent) text-(length:--text-sm) font-semibold">
              {i + 1}
            </span>
            <span className="text-(length:--text-sm) text-(--color-fg-default) pt-(--space-1)">
              {t(`dashboard.${key}` as const)}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(FIRST_RUN_LS_KEY) === "1";
  } catch {
    return false;
  }
}
