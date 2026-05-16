import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";

import { DestructiveConfirm } from "@/components/DestructiveConfirm";
import { SecretField } from "@/components/SecretField";
import { TypeToConfirmDialog } from "@/components/TypeToConfirmDialog";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useAuthReprompt, REPROMPT_CANCELLED } from "@/hooks/useAuthReprompt";
import {
  useClearCredential,
  useCreateTarget,
  useDeleteTarget,
  useRevealCredential,
  useSetCredential,
  useUpdateTarget,
} from "@/hooks/useTargetsAdmin";
import { useTargets } from "@/hooks/useTargets";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { TARGET_TYPE_SPECS } from "@/lib/targetTypeSpecs";
import type { TargetT, TargetTypeT } from "@/lib/schemas/targets";
import { t } from "@/messages";

export interface PersistentTargetTabProps {
  readonly type: TargetTypeT;
}

interface FormShape {
  label: string;
  url: string;
  enabled: boolean;
}

export function PersistentTargetTab(props: PersistentTargetTabProps): ReactNode {
  const { type } = props;
  const spec = TARGET_TYPE_SPECS[type];
  const { targets } = useTargets();
  const existing = targets.find((tg) => tg.type === type) ?? null;

  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {spec.displayLabel}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {spec.urlHint}
      </p>
      <TargetFormSurface
        type={type}
        existing={existing}
        defaultLabel={spec.defaultLabel}
        defaultUrl={spec.defaultUrl}
        presetUrls={spec.presetUrls}
      />
    </div>
  );
}

interface TargetFormSurfaceProps {
  readonly type: TargetTypeT;
  readonly existing: TargetT | null;
  readonly defaultLabel: string;
  readonly defaultUrl: string;
  readonly presetUrls: readonly string[];
}

function TargetFormSurface(props: TargetFormSurfaceProps): ReactNode {
  const { type, existing, defaultLabel, defaultUrl, presetUrls } = props;
  const create = useCreateTarget();
  const update = useUpdateTarget();
  const remove = useDeleteTarget();
  const reprompt = useAuthReprompt();
  const navigate = useNavigate();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const form = useForm<FormShape>({
    defaultValues: {
      label: existing?.label ?? defaultLabel,
      url: existing?.url ?? defaultUrl,
      enabled: existing?.enabled ?? true,
    },
  });
  useEffect(() => {
    form.reset({
      label: existing?.label ?? defaultLabel,
      url: existing?.url ?? defaultUrl,
      enabled: existing?.enabled ?? true,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing?.id]);

  const onSave = form.handleSubmit(async (vals) => {
    try {
      if (existing === null) {
        await create.mutateAsync({
          type,
          label: vals.label,
          url: vals.url,
          enabled: vals.enabled,
          settings: {},
        });
      } else {
        await update.mutateAsync({
          id: existing.id,
          body: { label: vals.label, url: vals.url, enabled: vals.enabled },
        });
      }
      form.reset(vals);
    } catch {
      /* inline */
    }
  });

  const onDelete = async (): Promise<void> => {
    if (existing === null) return;
    try {
      const grantId = await reprompt("delete_target");
      await remove.mutateAsync({ id: existing.id, grantId });
      void navigate("/settings/targets/twitch", { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  const presetSelected = form.watch("url");

  return (
    <form onSubmit={onSave}>
      <SettingsSection
        title={t("targetTab.identitySection")}
        footer={
          <button
            type="submit"
            disabled={!form.formState.isDirty || form.formState.isSubmitting}
            className={cn(
              "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
              "bg-(--color-accent) hover:bg-(--color-accent-strong)",
              (!form.formState.isDirty || form.formState.isSubmitting) &&
                "opacity-50 cursor-not-allowed",
            )}
          >
            {form.formState.isSubmitting ? t("settings.saving") : t("settings.save")}
          </button>
        }
      >
        <label className="block">
          <span className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
            {t("targetTab.labelInput")}
          </span>
          <input
            type="text"
            {...form.register("label", { required: true, maxLength: 128 })}
            className={cn(
              "mt-(--space-1) h-10 w-full rounded-(--radius-md) border bg-(--color-bg-base)",
              "border-(--color-border-subtle) px-(--space-3)",
              "text-(length:--text-sm) text-(--color-fg-strong)",
              "focus:border-(--color-accent) focus:outline-none",
            )}
          />
        </label>
        <label className="block">
          <span className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
            {t("targetTab.urlPresetLabel")}
          </span>
          {presetUrls.length > 0 ? (
            <select
              value={
                presetUrls.includes(presetSelected) ? presetSelected : "__custom__"
              }
              onChange={(e) => {
                if (e.target.value === "__custom__") {
                  form.setValue("url", "", { shouldDirty: true });
                } else {
                  form.setValue("url", e.target.value, { shouldDirty: true });
                }
              }}
              className={cn(
                "mt-(--space-1) h-10 w-full rounded-(--radius-md) border bg-(--color-bg-base)",
                "border-(--color-border-subtle) px-(--space-3)",
                "text-(length:--text-sm) text-(--color-fg-strong)",
                "focus:border-(--color-accent) focus:outline-none",
              )}
            >
              {presetUrls.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
              <option value="__custom__">{t("targetTab.urlCustom")}</option>
            </select>
          ) : null}
          {(!presetUrls.includes(presetSelected) || presetUrls.length === 0) && (
            <input
              type="text"
              {...form.register("url", { required: true, maxLength: 2048 })}
              placeholder={t("targetTab.urlCustomInput")}
              className={cn(
                "mt-(--space-2) h-10 w-full rounded-(--radius-md) border bg-(--color-bg-base)",
                "border-(--color-border-subtle) px-(--space-3)",
                "font-mono text-(length:--text-sm) text-(--color-fg-strong)",
                "focus:border-(--color-accent) focus:outline-none",
              )}
            />
          )}
        </label>
        <label className="flex items-center gap-(--space-3)">
          <input
            type="checkbox"
            {...form.register("enabled")}
            className="h-4 w-4 accent-(--color-accent)"
          />
          <span className="text-(length:--text-sm) text-(--color-fg-strong)">
            {t("targetTab.enabledLabel")}
          </span>
        </label>
      </SettingsSection>

      {existing !== null && (
        <>
          <StreamKeySection target={existing} />
          <DangerZone
            target={existing}
            onDeleteClick={() => setDeleteOpen(true)}
          />
          <TypeToConfirmDialog
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
            title={t("targetTab.deleteConfirmTitle")}
            body={t("targetTab.deleteConfirmBody")}
            cannotUndo={t("general.regenerateConfirmCannotUndo")}
            phrase={existing.label}
            confirmLabel={t("targetTab.deleteTarget")}
            onConfirm={() => void onDelete()}
          />
        </>
      )}
    </form>
  );
}

// ----------------- Stream-key section -----------------

function StreamKeySection({ target }: { readonly target: TargetT }): ReactNode {
  const reprompt = useAuthReprompt();
  const setCred = useSetCredential();
  const clearCred = useClearCredential();
  const reveal = useRevealCredential();
  const [revealed, setRevealed] = useState<string | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [changing, setChanging] = useState(false);
  const [newKey, setNewKey] = useState("");

  useEffect(() => {
    if (revealed === null) return;
    const start = performance.now();
    const tick = (): void => {
      const remaining = Math.max(
        0,
        Math.ceil(60 - (performance.now() - start) / 1000),
      );
      setCountdown(remaining);
      if (remaining <= 0) setRevealed(null);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [revealed]);

  // Reviewer M-5: clear the plaintext on unmount so the React fiber
  // doesn't carry the live key string into post-unmount heap residency.
  useEffect(() => {
    return () => {
      setRevealed(null);
    };
  }, []);

  const onReveal = async (): Promise<void> => {
    try {
      const grantId = await reprompt("reveal_stream_key");
      const r = await reveal.mutateAsync({ id: target.id, grantId });
      setRevealed(r.plaintext);
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  const onSaveKey = async (): Promise<void> => {
    if (!newKey.trim()) return;
    try {
      await setCred.mutateAsync({ id: target.id, streamKey: newKey });
      setNewKey("");
      setChanging(false);
    } catch {
      /* inline */
    }
  };

  const onClear = async (): Promise<void> => {
    try {
      const grantId = await reprompt("clear_credential");
      await clearCred.mutateAsync({ id: target.id, grantId });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  return (
    <SettingsSection title={t("targetTab.streamKeySection")}>
      <SecretField
        variant="masked"
        last4={target.credential_last4}
        revealedValue={revealed ?? undefined}
        revealCountdownLabel={
          revealed !== null
            ? t("secret.countdownHidesIn", {
                time: `0:${String(countdown).padStart(2, "0")}`,
              })
            : undefined
        }
        onRequestReveal={() => void onReveal()}
        onHide={() => setRevealed(null)}
        ariaLabel={t("targetTab.streamKeySection")}
      />
      <div className="flex flex-wrap gap-(--space-2)">
        <button
          type="button"
          onClick={() => setChanging((v) => !v)}
          className={cn(
            "h-9 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm) font-medium",
            "border border-(--color-border-subtle) text-(--color-fg-default) hover:bg-(--color-bg-sunken)",
          )}
        >
          {t("targetTab.changeKey")}
        </button>
        {target.has_credential && (
          <DestructiveConfirm
            trigger={
              <button
                type="button"
                className="h-9 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm) text-(--color-error) hover:bg-(--color-error-faint)"
              >
                {t("targetTab.clearKey")}
              </button>
            }
            body={
              <>
                <p>{t("targetTab.clearKeyConfirm")}</p>
                <p className="mt-(--space-2) text-(--color-fg-muted)">
                  {t("targetTab.clearKeyConfirmBody")}
                </p>
              </>
            }
            confirmLabel={t("targetTab.clearKey")}
            onConfirm={() => void onClear()}
          />
        )}
      </div>
      {changing && (
        <div className="rounded-(--radius-md) border border-(--color-border-subtle) p-(--space-3)">
          <SecretField
            variant="entry"
            value={newKey}
            onChange={setNewKey}
            ariaLabel={t("targetTab.streamKeySection")}
          />
          <div className="mt-(--space-3) flex justify-end gap-(--space-2)">
            <button
              type="button"
              onClick={() => {
                setChanging(false);
                setNewKey("");
              }}
              className="h-9 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm) text-(--color-fg-default) hover:bg-(--color-bg-sunken)"
            >
              {t("common.cancel")}
            </button>
            <button
              type="button"
              onClick={() => void onSaveKey()}
              disabled={!newKey.trim() || setCred.isPending}
              className={cn(
                "h-9 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm) font-medium text-white",
                "bg-(--color-accent) hover:bg-(--color-accent-strong)",
                (!newKey.trim() || setCred.isPending) && "opacity-50 cursor-not-allowed",
              )}
            >
              {t("targetTab.saveKey")}
            </button>
          </div>
        </div>
      )}
    </SettingsSection>
  );
}

function DangerZone({
  target,
  onDeleteClick,
}: {
  readonly target: TargetT;
  readonly onDeleteClick: () => void;
}): ReactNode {
  return (
    <SettingsSection title={t("targetTab.dangerZone")}>
      <button
        type="button"
        onClick={onDeleteClick}
        className={cn(
          "self-start h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
          "bg-(--color-error) hover:bg-(--color-error)/85",
        )}
      >
        {t("targetTab.deleteTarget")} {target.label !== "" && `“${target.label}”`}
      </button>
    </SettingsSection>
  );
}
