import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";

import { Banner } from "@/components/Banner";
import { Button } from "@/components/Button";
import { DestructiveConfirm } from "@/components/DestructiveConfirm";
import { FormField } from "@/components/FormField";
import { Input } from "@/components/Input";
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
  // Hex Audit CR-F12 (slice 10): surface server-side save failures
  // (4xx / 5xx / network) to the operator instead of silently
  // returning the form to idle. Pre-slice-10 the catch swallowed
  // errors and the only signal was the absence of a redirect/toast,
  // which an operator easily missed.
  const [saveError, setSaveError] = useState<string | null>(null);

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
    setSaveError(null);
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
    } catch (err) {
      // Hex Audit CR-F12 (slice 10): show the error so the operator
      // can react. ApiError carries the wire-level ErrorCode the
      // backend returned; anything else (network/timeout) collapses
      // to a generic "try again" message.
      if (err instanceof ApiError) {
        setSaveError(err.message || t("targetTab.saveError"));
      } else {
        setSaveError(t("targetTab.saveError"));
      }
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

  // For a new target the form is pre-filled with valid defaults
  // (label, URL, enabled=true) so `formState.isDirty` is false out of
  // the gate. Gating Save on `isDirty` would force the user to type
  // an arbitrary change before they can create the target — a trap
  // discovered by the operator post-v1.1.3. We require dirty only on
  // updates, where a no-op Save is genuinely wasted work.
  const submitLocked = existing !== null && !form.formState.isDirty;

  return (
    <form onSubmit={onSave}>
      {/* Hex Audit CR-F12 (slice 10): save-failure banner. Cleared on
          every submit attempt; re-populated on catch. */}
      {saveError !== null && (
        <Banner variant="error" className="mb-(--space-4)">
          {saveError}
        </Banner>
      )}
      <SettingsSection
        title={t("targetTab.identitySection")}
        footer={
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={submitLocked}
            loading={form.formState.isSubmitting}
          >
            {form.formState.isSubmitting
              ? t("settings.saving")
              : existing === null
                ? t("targetTab.createTarget")
                : t("settings.save")}
          </Button>
        }
      >
        <FormField label={t("targetTab.labelInput")}>
          <FormField.Input
            type="text"
            {...form.register("label", { required: true, maxLength: 128 })}
          />
        </FormField>
        {/* Slice-6: preset URL Select; when no presets ship OR the
            operator picks "__custom__", a sibling Input collects the
            raw URL. The "__custom__" sentinel stays at the caller level
            per UX-architect memo §2.2 — a primitive that knew about
            sentinel values would leak target-spec semantics into the
            design system. The custom Input renders OUTSIDE the
            FormField (avoids two controls under one `htmlFor`) and uses
            its placeholder as its own label, matching the pre-slice-6
            UX. */}
        {presetUrls.length > 0 && (
          <FormField label={t("targetTab.urlPresetLabel")}>
            <FormField.Select
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
              options={[
                ...presetUrls.map((u) => ({ value: u, label: u })),
                { value: "__custom__", label: t("targetTab.urlCustom") },
              ]}
            />
          </FormField>
        )}
        {(!presetUrls.includes(presetSelected) || presetUrls.length === 0) && (
          <Input
            type="text"
            mono
            aria-label={t("targetTab.urlCustomInput")}
            {...form.register("url", { required: true, maxLength: 2048 })}
            placeholder={t("targetTab.urlCustomInput")}
          />
        )}
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
          {/* Stream key is hidden for disabled targets — a disabled
           * target can't broadcast, so credential management for it
           * is noise. Re-enable to manage the key. Danger zone stays
           * visible so the user can always delete a stale target,
           * including disabled ones. Gated on the SAVED state
           * (`existing.enabled`) not the form-watched value so the
           * visibility transition is atomic with Save — no flicker
           * mid-edit. */}
          {existing.enabled && (
            <>
              {!existing.has_credential &&
                TARGET_TYPE_SPECS[type].persistentStreamKey && (
                  <Banner variant="warn" title={t("targetTab.missingKeyTitle")}>
                    {t("targetTab.missingKeyBody")}
                  </Banner>
                )}
              <StreamKeySection target={existing} />
            </>
          )}
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
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={() => setChanging((v) => !v)}
        >
          {t("targetTab.changeKey")}
        </Button>
        {target.has_credential && (
          <DestructiveConfirm
            trigger={
              <Button
                type="button"
                variant="danger-ghost"
                size="md"
              >
                {t("targetTab.clearKey")}
              </Button>
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
            <Button
              type="button"
              variant="ghost"
              size="md"
              onClick={() => {
                setChanging(false);
                setNewKey("");
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="primary"
              size="md"
              onClick={() => void onSaveKey()}
              disabled={!newKey.trim()}
              loading={setCred.isPending}
            >
              {t("targetTab.saveKey")}
            </Button>
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
      <Button
        type="button"
        variant="danger"
        size="md"
        onClick={onDeleteClick}
        className="self-start"
      >
        {t("targetTab.deleteTarget")} {target.label !== "" && `“${target.label}”`}
      </Button>
    </SettingsSection>
  );
}
