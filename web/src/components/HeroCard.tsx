import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Banner } from "./Banner";
import { Button } from "./Button";
import { InlinePromptCard } from "./InlinePromptCard";
import { apiFetch, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { TARGETS_QUERY_KEY, type TargetWithSnapshot } from "@/hooks/useTargets";
import { RUN_STATE_QUERY_KEY } from "@/hooks/useRunState";
import { t } from "@/messages";

import type { RunStateT } from "@/lib/schemas/run";

const FIRST_RUN_LS_KEY = "restream-plus.firstRunHintDismissed";

export interface HeroCardProps {
  readonly runState: RunStateT;
  readonly enabledTargets: readonly TargetWithSnapshot[];
  readonly firstRunComplete: boolean;
  readonly ingestKeyLast4: string | null;
  readonly aggregateBitrate: number | null;
  readonly runningCount: number;
  readonly totalEnabled: number;
  readonly totalDrops: number;
}

/**
 * Phase 8 hero — START/STOP + state-driven body per phase-8-design-
 * memo §I.
 *
 * Click on START routes through the VK-needs-key gate (UX Q4 + §J):
 * any enabled VK target without a credential triggers the
 * InlinePromptCard; the user can either paste keys then start, or
 * skip (the supervisor's _credentials_satisfied gate filters skipped
 * VKs to DISABLED_MISCONFIGURED for the session).
 */
export function HeroCard({
  runState,
  enabledTargets,
  firstRunComplete,
  ingestKeyLast4,
  aggregateBitrate,
  runningCount,
  totalEnabled,
  totalDrops,
}: HeroCardProps): ReactNode {
  const queryClient = useQueryClient();
  const [vkPromptOpen, setVkPromptOpen] = useState(false);
  const [confirmingStop, setConfirmingStop] = useState(false);
  const [hintDismissed, setHintDismissed] = useState(() => readDismissed());

  // Auto-dismiss the first-run hint on first LIVE transition.
  useEffect(() => {
    if (runState === "live" && !hintDismissed) {
      window.localStorage.setItem(FIRST_RUN_LS_KEY, "1");
      setHintDismissed(true);
    }
  }, [runState, hintDismissed]);

  const vkNeedingKey = enabledTargets.filter(
    (tgt) => tgt.type === "vk_live" && !tgt.has_credential,
  );

  const startMutation = useMutation<unknown, ApiError, void>({
    mutationFn: () =>
      apiFetch("run/start", { method: "POST" }),
    onError: (err) => {
      if (err.code === "illegal_run_state") {
        void queryClient.invalidateQueries({ queryKey: RUN_STATE_QUERY_KEY });
      }
    },
  });

  const stopMutation = useMutation<unknown, ApiError, void>({
    mutationFn: () =>
      apiFetch("run/stop", { method: "POST" }),
    onError: (err) => {
      if (err.code === "illegal_run_state") {
        void queryClient.invalidateQueries({ queryKey: RUN_STATE_QUERY_KEY });
      }
    },
  });

  const credentialMutation = useMutation<unknown, ApiError, { id: string; key: string }>({
    mutationFn: ({ id, key }) =>
      apiFetch(`targets/${id}/credential`, {
        method: "PUT",
        json: { stream_key: key },
      }),
  });

  async function handleUseAndStart(perTarget: ReadonlyMap<string, string>): Promise<void> {
    for (const [id, key] of perTarget) {
      // Sequential — backend serialises DB writes anyway.
       
      await credentialMutation.mutateAsync({ id, key });
    }
    await queryClient.invalidateQueries({ queryKey: TARGETS_QUERY_KEY });
    startMutation.mutate();
    setVkPromptOpen(false);
  }

  function handleSkip(): void {
    startMutation.mutate();
    setVkPromptOpen(false);
  }

  function onStartClick(): void {
    if (vkNeedingKey.length > 0) {
      setVkPromptOpen(true);
      return;
    }
    startMutation.mutate();
  }

  function onStopClick(): void {
    if (runState === "live") {
      setConfirmingStop(true);
      return;
    }
    stopMutation.mutate();
  }

  const showFirstRunHint = !firstRunComplete && !hintDismissed && runState === "offline";

  return (
    <section
      id="dashboard-hero"
      className={cn(
        "mx-auto w-full max-w-[720px] rounded-(--radius-xl) bg-(--color-bg-elevated)",
        "border border-(--color-border-subtle) shadow-(--shadow-sm) p-(--space-8)",
        // Reviewer M-3: --color-* tokens ship full `hsl(...)` values per
        // Phase 7 §D — wrapping in hsl() again was invalid CSS and
        // silently hid the live-ambient ring.
        runState === "live" &&
          "[box-shadow:0_0_0_1px_var(--color-live-faint),var(--shadow-sm)]",
      )}
    >
      <div className="flex flex-col items-center gap-(--space-6)">
        <HeroButton
          runState={runState}
          confirmingStop={confirmingStop}
          onStart={onStartClick}
          onStop={onStopClick}
          onConfirmStop={() => {
            stopMutation.mutate();
            setConfirmingStop(false);
          }}
          onCancelStop={() => setConfirmingStop(false)}
          isPendingStart={startMutation.isPending}
          isPendingStop={stopMutation.isPending}
        />
        <HeroBody
          runState={runState}
          ingestKeyLast4={ingestKeyLast4}
          aggregateBitrate={aggregateBitrate}
          runningCount={runningCount}
          totalEnabled={totalEnabled}
          totalDrops={totalDrops}
        />
        {showFirstRunHint && <FirstRunHint />}
      </div>
      {vkPromptOpen && vkNeedingKey.length > 0 && (
        <InlinePromptCard
          vkTargets={vkNeedingKey}
          submitting={startMutation.isPending || credentialMutation.isPending}
          onUseAndStart={(perTarget) => {
            void handleUseAndStart(perTarget);
          }}
          onSkip={handleSkip}
        />
      )}
    </section>
  );
}

interface HeroButtonProps {
  readonly runState: RunStateT;
  readonly confirmingStop: boolean;
  readonly onStart: () => void;
  readonly onStop: () => void;
  readonly onConfirmStop: () => void;
  readonly onCancelStop: () => void;
  readonly isPendingStart: boolean;
  readonly isPendingStop: boolean;
}

function HeroButton({
  runState,
  confirmingStop,
  onStart,
  onStop,
  onConfirmStop,
  onCancelStop,
  isPendingStart,
  isPendingStop,
}: HeroButtonProps): ReactNode {
  const confirmStopRef = useRef<HTMLButtonElement | null>(null);
  const cancelStopRef = useRef<HTMLButtonElement | null>(null);

  // 3s auto-revert + focus to Stop pill on morph.
  useEffect(() => {
    if (!confirmingStop) return;
    confirmStopRef.current?.focus();
    const handle = window.setTimeout(onCancelStop, 3000);
    return () => window.clearTimeout(handle);
  }, [confirmingStop, onCancelStop]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
    if (e.key === "ArrowRight") {
      e.preventDefault();
      cancelStopRef.current?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      confirmStopRef.current?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancelStop();
    }
  }

  if (confirmingStop) {
    return (
      <div
        role="group"
        aria-label={t("hero.stopConfirmAria")}
        onKeyDown={onKeyDown}
        onMouseLeave={onCancelStop}
        className="inline-flex h-20 min-w-[280px] max-w-[420px] gap-px overflow-hidden rounded-md"
      >
        <Button
          ref={confirmStopRef}
          variant="danger"
          size="lg"
          className="flex-1 rounded-r-none rounded-l-md text-(length:--text-xl)"
          onClick={onConfirmStop}
          loading={isPendingStop}
          data-hero-button
        >
          {t("hero.stopConfirm")}
        </Button>
        <Button
          ref={cancelStopRef}
          variant="secondary"
          size="lg"
          className="flex-1 rounded-l-none rounded-r-md text-(length:--text-xl)"
          onClick={onCancelStop}
        >
          {t("hero.stopCancel")}
        </Button>
      </div>
    );
  }

  switch (runState) {
    case "offline":
      return (
        <Button variant="primary" size="xl" onClick={onStart} loading={isPendingStart} data-hero-button>
          {t("hero.start")}
        </Button>
      );
    case "starting":
      return (
        <Button variant="primary" size="xl" disabled loading data-hero-button>
          {t("hero.starting")}
        </Button>
      );
    case "armed":
      return (
        <Button variant="danger" size="xl" onClick={onStop} loading={isPendingStop} data-hero-button>
          {t("hero.stop")}
        </Button>
      );
    case "live":
      return (
        <Button variant="danger" size="xl" onClick={onStop} loading={isPendingStop} data-hero-button>
          {t("hero.stop")}
        </Button>
      );
    case "stopping":
      return (
        <Button variant="danger" size="xl" disabled loading data-hero-button>
          {t("hero.stopping")}
        </Button>
      );
    case "error":
      return (
        <Button
          variant="primary"
          size="xl"
          onClick={onStart}
          loading={isPendingStart}
          className="ring-1 ring-(--color-error)"
          data-hero-button
        >
          {t("hero.start")}
        </Button>
      );
  }
}

interface HeroBodyProps {
  readonly runState: RunStateT;
  readonly ingestKeyLast4: string | null;
  readonly aggregateBitrate: number | null;
  readonly runningCount: number;
  readonly totalEnabled: number;
  readonly totalDrops: number;
}

function HeroBody({
  runState,
  ingestKeyLast4,
  aggregateBitrate,
  runningCount,
  totalEnabled,
  totalDrops,
}: HeroBodyProps): ReactNode {
  const ingestUrl = `rtmps://${window.location.host}/live`;
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
        <p className="text-(length:--text-base) text-(--color-fg-default)">
          {t("hero.liveSummary", {
            runningCount,
            totalEnabled,
            bitrate: aggregateBitrate === null ? "—" : aggregateBitrate.toFixed(1),
            drops: totalDrops,
          })}
        </p>
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

function FirstRunHint(): ReactNode {
  return (
    <div className="w-full mt-(--space-2) rounded-(--radius-md) bg-(--color-info-faint) border-l-2 border-(--color-info) p-(--space-4)">
      <ol className="flex flex-col gap-(--space-2)">
        {(["firstRunStep1", "firstRunStep2", "firstRunStep3"] as const).map((key, i) => (
          <li key={key} className="flex items-start gap-(--space-3)">
            <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-(--color-info) text-white text-(length:--text-xs) font-semibold">
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
