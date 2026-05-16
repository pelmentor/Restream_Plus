import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";

import { CopyToClipboard } from "@/components/CopyToClipboard";
import { OneTimeRevealBanner } from "@/components/OneTimeRevealBanner";
import { SecretField } from "@/components/SecretField";
import { Slider } from "@/components/Slider";
import { TypeToConfirmDialog } from "@/components/TypeToConfirmDialog";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useAuthReprompt, REPROMPT_CANCELLED } from "@/hooks/useAuthReprompt";
import {
  useRevealIngestKey,
  useRotateIngestKey,
  useSettings,
  useUpdateSettings,
} from "@/hooks/useSettings";
import { cn } from "@/lib/cn";
import { ApiError } from "@/lib/api";
import { t } from "@/messages";

interface RevealedKey {
  readonly value: string;
  readonly expiresAt: number;
}

export function GeneralTab(): ReactNode {
  const { data, isPending } = useSettings();
  const update = useUpdateSettings();
  const reveal = useRevealIngestKey();
  const rotate = useRotateIngestKey();
  const reprompt = useAuthReprompt();
  const [revealed, setRevealed] = useState<RevealedKey | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [oneTime, setOneTime] = useState<{ plaintext: string } | null>(null);
  const [confirmRotate, setConfirmRotate] = useState(false);

  const form = useForm<{ idle: number; logs: number }>({
    defaultValues: { idle: 60, logs: 14 },
  });
  useEffect(() => {
    if (data) {
      form.reset({
        idle: data.idle_timeout_seconds,
        logs: data.log_retention_days,
      });
    }
  }, [data, form]);

  // 60 s reveal countdown.
  useEffect(() => {
    if (revealed === null) {
      setCountdown(0);
      return;
    }
    const tick = (): void => {
      const remaining = Math.max(
        0,
        Math.ceil((revealed.expiresAt - performance.now()) / 1000),
      );
      setCountdown(remaining);
      if (remaining <= 0) setRevealed(null);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [revealed]);

  // Reviewer M-5: drop the plaintext ingest key on unmount so the
  // React fiber doesn't carry it into post-unmount heap residency.
  useEffect(() => {
    return () => {
      setRevealed(null);
      setOneTime(null);
    };
  }, []);

  if (isPending || !data) {
    return (
      <div className="text-(length:--text-sm) text-(--color-fg-muted)">
        {t("common.loading")}
      </div>
    );
  }

  const isDirty = form.formState.isDirty;
  const onSave = form.handleSubmit(async (vals) => {
    try {
      await update.mutateAsync({
        idle_timeout_seconds: vals.idle,
        log_retention_days: vals.logs,
      });
      form.reset(vals);
    } catch {
      /* surfaced inline via mutation.isError */
    }
  });

  const onReveal = async (): Promise<void> => {
    try {
      const grantId = await reprompt("reveal_ingest_key");
      const r = await reveal.mutateAsync({ grantId });
      setRevealed({
        value: r.plaintext,
        expiresAt: performance.now() + 60_000,
      });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
      /* otherwise: inline */
    }
  };

  const onRotateConfirm = async (): Promise<void> => {
    try {
      const grantId = await reprompt("regenerate_ingest_key");
      const r = await rotate.mutateAsync({ grantId });
      setOneTime({ plaintext: r.plaintext });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  const ingestUrl = `${window.location.protocol}//${window.location.hostname}:1935/live`;

  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {t("settings.pageGeneralTitle")}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {t("settings.pageGeneralSubtitle")}
      </p>

      {oneTime !== null && (
        <OneTimeRevealBanner
          title={t("reveal.bannerTitle")}
          body={t("reveal.bannerBody")}
          value={oneTime.plaintext}
          ariaLabel={t("general.ingestKeyLabel")}
          onDismiss={() => setOneTime(null)}
        />
      )}

      <SettingsSection title={t("general.ingestSection")}>
        <Field label={t("general.ingestUrlLabel")} helper={t("general.ingestUrlHelper")}>
          <div className="flex items-center gap-(--space-2)">
            <div
              className={cn(
                "h-10 flex-1 min-w-0 rounded-(--radius-md) border bg-(--color-bg-sunken)",
                "border-(--color-border-subtle) px-(--space-3)",
                "flex items-center font-mono text-(length:--text-sm) text-(--color-fg-strong) truncate",
              )}
            >
              {ingestUrl}
            </div>
            <CopyToClipboard value={ingestUrl} variant="standalone" />
          </div>
        </Field>
        <Field label={t("general.ingestKeyLabel")}>
          <SecretField
            variant="masked"
            last4={data.ingest_key_last4}
            revealedValue={revealed?.value}
            revealCountdownLabel={
              revealed !== null
                ? t("secret.countdownHidesIn", {
                    time: `0:${String(countdown).padStart(2, "0")}`,
                  })
                : undefined
            }
            onRequestReveal={() => void onReveal()}
            onHide={() => setRevealed(null)}
            ariaLabel={t("general.ingestKeyLabel")}
          />
          <div className="mt-(--space-2)">
            <button
              type="button"
              onClick={() => setConfirmRotate(true)}
              className={cn(
                "h-9 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm) font-medium",
                "border border-(--color-error) text-(--color-error) hover:bg-(--color-error-faint)",
              )}
            >
              {t("general.regenerate")}
            </button>
          </div>
        </Field>
      </SettingsSection>

      <form onSubmit={onSave}>
        <SettingsSection
          title={t("general.runBehaviorSection")}
          footer={
            <>
              <button
                type="button"
                disabled={!isDirty}
                onClick={() => form.reset()}
                className={cn(
                  "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm)",
                  "text-(--color-fg-default) hover:bg-(--color-bg-sunken)",
                  !isDirty && "opacity-50 cursor-not-allowed",
                )}
              >
                {t("settings.discard")}
              </button>
              <button
                type="submit"
                disabled={!isDirty || update.isPending}
                className={cn(
                  "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
                  "bg-(--color-accent) hover:bg-(--color-accent-strong)",
                  (!isDirty || update.isPending) &&
                    "opacity-50 cursor-not-allowed",
                )}
              >
                {update.isPending ? t("settings.saving") : t("settings.save")}
              </button>
            </>
          }
        >
          <Field
            label={t("general.idleTimeoutLabel")}
            helper={t("general.idleTimeoutHelper")}
          >
            <Slider
              value={form.watch("idle")}
              onValueChange={(v) => form.setValue("idle", v, { shouldDirty: true })}
              min={10}
              max={600}
              step={5}
              ariaLabel={t("general.idleTimeoutLabel")}
              valueText={t("slider.seconds", { n: form.watch("idle") })}
              displayValue={`${form.watch("idle")} s`}
            />
          </Field>
          <Field
            label={t("general.logRetentionLabel")}
            helper={t("general.logRetentionHelper")}
          >
            <Slider
              value={form.watch("logs")}
              onValueChange={(v) => form.setValue("logs", v, { shouldDirty: true })}
              min={1}
              max={90}
              step={1}
              ariaLabel={t("general.logRetentionLabel")}
              valueText={`${form.watch("logs")}`}
              displayValue={`${form.watch("logs")}`}
            />
          </Field>
          {update.isError && (
            <p className="text-(length:--text-xs) text-(--color-error)">
              {t("settings.sectionSaveFailed")}
            </p>
          )}
        </SettingsSection>
      </form>

      <TypeToConfirmDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title={t("general.regenerateConfirmTitle")}
        body={t("general.regenerateConfirmBody")}
        cannotUndo={t("general.regenerateConfirmCannotUndo")}
        phrase={t("general.regeneratePhrase")}
        confirmLabel={t("general.regenerate")}
        onConfirm={() => void onRotateConfirm()}
      />
    </div>
  );
}

function Field({
  label,
  helper,
  children,
}: {
  readonly label: string;
  readonly helper?: ReactNode;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <div className="flex flex-col gap-(--space-2)">
      <span className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
        {label}
      </span>
      {children}
      {helper !== undefined && (
        <p className="text-(length:--text-xs) text-(--color-fg-muted)">
          {helper}
        </p>
      )}
    </div>
  );
}
