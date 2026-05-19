import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { Info } from "@phosphor-icons/react";

import { Button } from "@/components/Button";
import { FormField } from "@/components/FormField";
import { SecretField } from "@/components/SecretField";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useAuthReprompt, REPROMPT_CANCELLED } from "@/hooks/useAuthReprompt";
import {
  useCreateTarget,
  useSetCredential,
  useUpdateTarget,
} from "@/hooks/useTargetsAdmin";
import { useTargets } from "@/hooks/useTargets";
import { ApiError } from "@/lib/api";
import { TARGET_TYPE_SPECS } from "@/lib/targetTypeSpecs";
import { t } from "@/messages";

export function VKTab(): ReactNode {
  const spec = TARGET_TYPE_SPECS.vk_live;
  const { targets } = useTargets();
  const existing = targets.find((tg) => tg.type === "vk_live") ?? null;
  const create = useCreateTarget();
  const update = useUpdateTarget();
  const reprompt = useAuthReprompt();
  const setCred = useSetCredential();
  const [advanced, setAdvanced] = useState(false);
  const [pasteKey, setPasteKey] = useState("");

  const form = useForm<{ label: string; url: string; enabled: boolean }>({
    defaultValues: {
      label: existing?.label ?? spec.defaultLabel,
      url: existing?.url ?? spec.defaultUrl,
      enabled: existing?.enabled ?? true,
    },
  });

  const onSave = form.handleSubmit(async (vals) => {
    try {
      if (existing === null) {
        await create.mutateAsync({
          type: "vk_live",
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

  const onSaveKey = async (): Promise<void> => {
    if (!existing || !pasteKey.trim()) return;
    try {
      const _ = await reprompt("reveal_stream_key");
      void _;
      await setCred.mutateAsync({ id: existing.id, streamKey: pasteKey });
      setPasteKey("");
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {spec.displayLabel}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {spec.urlHint}
      </p>
      <form onSubmit={onSave}>
        <SettingsSection
          title={t("targetTab.identitySection")}
          footer={
            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={!form.formState.isDirty}
              loading={form.formState.isSubmitting}
            >
              {t("settings.save")}
            </Button>
          }
        >
          <FormField label={t("targetTab.labelInput")}>
            <FormField.Input
              type="text"
              {...form.register("label", { required: true, maxLength: 128 })}
            />
          </FormField>
          <FormField label={t("targetTab.urlPresetLabel")}>
            <FormField.Select
              {...form.register("url")}
              options={spec.presetUrls.map((u) => ({ value: u, label: u }))}
            />
          </FormField>
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
      </form>

      <SettingsSection title={t("targetTab.streamKeySection")}>
        <div className="flex items-start gap-(--space-3) rounded-(--radius-md) border border-(--color-info) bg-(--color-info-faint) p-(--space-3)">
          <Info
            className="mt-0.5 h-5 w-5 shrink-0 text-(--color-info)"
            weight="regular"
            aria-hidden="true"
          />
          <div className="text-(length:--text-sm) text-(--color-fg-strong)">
            <p className="font-semibold">{t("targetTab.vkInfoTitle")}</p>
            <p className="mt-(--space-1) text-(--color-fg-default)">
              {t("targetTab.vkInfoBody")}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="link"
          size="sm"
          onClick={() => setAdvanced((v) => !v)}
          className="self-start"
        >
          {t("targetTab.vkAdvancedToggle")}
        </Button>
        {advanced && existing !== null && (
          <div className="rounded-(--radius-md) border border-(--color-border-subtle) p-(--space-3)">
            <SecretField
              variant="paste_only"
              value={pasteKey}
              onChange={setPasteKey}
              ariaLabel={t("targetTab.streamKeySection")}
            />
            <div className="mt-(--space-3) flex justify-end">
              <Button
                type="button"
                variant="primary"
                size="md"
                onClick={() => void onSaveKey()}
                disabled={!pasteKey.trim()}
                loading={setCred.isPending}
              >
                {t("targetTab.saveForNext")}
              </Button>
            </div>
          </div>
        )}
      </SettingsSection>
    </div>
  );
}
