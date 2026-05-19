import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/Button";
import { FormField } from "@/components/FormField";
import { SecretField } from "@/components/SecretField";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useAuthReprompt, REPROMPT_CANCELLED } from "@/hooks/useAuthReprompt";
import {
  useCreateTarget,
  useDeleteTarget,
  useSetCredential,
  useUpdateTarget,
} from "@/hooks/useTargetsAdmin";
import { useTargets } from "@/hooks/useTargets";
import { ApiError } from "@/lib/api";
import type { TargetT } from "@/lib/schemas/targets";
import { t } from "@/messages";

export function CustomTab(): ReactNode {
  const { targets } = useTargets();
  const customs = targets.filter((tg) => tg.type === "custom");
  const [editing, setEditing] = useState<TargetT | "new" | null>(null);
  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {t("settings.tabCustom")}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {t("targetTab.addCustomTarget")}
      </p>
      <SettingsSection title={t("settings.tabCustom")}>
        {customs.length === 0 ? (
          <div className="rounded-(--radius-md) border border-dashed border-(--color-border-subtle) p-(--space-6) text-center">
            <p className="text-(length:--text-sm) text-(--color-fg-muted)">
              {t("targetTab.customEmpty")}
            </p>
            <Button
              type="button"
              variant="primary"
              size="md"
              onClick={() => setEditing("new")}
              className="mt-(--space-3)"
            >
              {t("targetTab.customListEmptyCta")}
            </Button>
          </div>
        ) : (
          <>
            <ul className="flex flex-col gap-(--space-2)">
              {customs.map((c) => (
                <CustomRow key={c.id} target={c} onEdit={() => setEditing(c)} />
              ))}
            </ul>
            <Button
              type="button"
              variant="primary"
              size="md"
              onClick={() => setEditing("new")}
              className="self-start"
            >
              {t("targetTab.addCustomTarget")}
            </Button>
          </>
        )}
      </SettingsSection>
      {editing !== null && (
        <CustomEditor
          existing={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function CustomRow({
  target,
  onEdit,
}: {
  readonly target: TargetT;
  readonly onEdit: () => void;
}): ReactNode {
  return (
    <li className="flex items-center gap-(--space-3) rounded-(--radius-md) border border-(--color-border-subtle) p-(--space-3)">
      <div className="flex-1 min-w-0">
        <p className="truncate text-(length:--text-sm) font-medium text-(--color-fg-strong)">
          {target.label}
        </p>
        <p className="truncate font-mono text-(length:--text-xs) text-(--color-fg-muted)">
          {target.url}
        </p>
      </div>
      <Button
        type="button"
        variant="link"
        size="sm"
        onClick={onEdit}
      >
        {t("security.customRowEdit")}
      </Button>
    </li>
  );
}

function CustomEditor({
  existing,
  onClose,
}: {
  readonly existing: TargetT | null;
  readonly onClose: () => void;
}): ReactNode {
  const create = useCreateTarget();
  const update = useUpdateTarget();
  const setCred = useSetCredential();
  const remove = useDeleteTarget();
  const reprompt = useAuthReprompt();
  const form = useForm<{
    label: string;
    url: string;
    enabled: boolean;
    streamKey: string;
  }>({
    defaultValues: {
      label: existing?.label ?? t("targetTab.customDefaultLabel"),
      url: existing?.url ?? "",
      enabled: existing?.enabled ?? true,
      streamKey: "",
    },
  });

  const onSave = form.handleSubmit(async (vals) => {
    try {
      let target: TargetT;
      if (existing === null) {
        target = await create.mutateAsync({
          type: "custom",
          label: vals.label,
          url: vals.url,
          enabled: vals.enabled,
          settings: {},
        });
      } else {
        target = await update.mutateAsync({
          id: existing.id,
          body: { label: vals.label, url: vals.url, enabled: vals.enabled },
        });
      }
      if (vals.streamKey.trim().length > 0) {
        await setCred.mutateAsync({ id: target.id, streamKey: vals.streamKey });
      }
      onClose();
    } catch {
      /* inline */
    }
  });

  const onDelete = async (): Promise<void> => {
    if (!existing) return;
    try {
      const grantId = await reprompt("delete_target");
      await remove.mutateAsync({ id: existing.id, grantId });
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  return (
    <SettingsSection
      title={existing === null ? t("targetTab.customNewTitle") : t("targetTab.customEditTitle")}
      footer={
        <>
          {existing && (
            <Button
              type="button"
              variant="danger-ghost"
              size="md"
              onClick={() => void onDelete()}
            >
              {t("targetTab.deleteTarget")}
            </Button>
          )}
          <Button type="button" variant="ghost" size="md" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            variant="primary"
            size="md"
            onClick={() => void onSave()}
            loading={form.formState.isSubmitting}
          >
            {t("settings.save")}
          </Button>
        </>
      }
    >
      <FormField label={t("targetTab.labelInput")}>
        <FormField.Input
          type="text"
          {...form.register("label", { required: true, maxLength: 128 })}
        />
      </FormField>
      <FormField label={t("targetTab.urlCustomInput")}>
        <FormField.Input
          type="text"
          mono
          placeholder="rtmps://example.com/app"
          {...form.register("url", { required: true, maxLength: 2048 })}
        />
      </FormField>
      <div className="flex items-center gap-(--space-3)">
        <input
          type="checkbox"
          {...form.register("enabled")}
          className="h-4 w-4 accent-(--color-accent)"
        />
        <span className="text-(length:--text-sm) text-(--color-fg-strong)">
          {t("targetTab.enabledLabel")}
        </span>
      </div>
      {/* Slice-6 reviewer H-2: SecretField owns its own input id +
          `aria-label`, so wrapping it in FormField would create a
          dangling `<label htmlFor>` pointing to a generated id that
          doesn't exist in the rendered DOM. Render the visible label
          inline and let SecretField's internal ariaLabel handle SR
          announcement. */}
      <div className="flex flex-col gap-(--space-1)">
        <span className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
          {t("targetTab.streamKeySection")}
        </span>
        <SecretField
          variant="entry"
          value={form.watch("streamKey")}
          onChange={(v) => form.setValue("streamKey", v, { shouldDirty: true })}
          ariaLabel={t("targetTab.streamKeySection")}
        />
      </div>
    </SettingsSection>
  );
}
