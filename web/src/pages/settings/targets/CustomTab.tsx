import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";

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
import { cn } from "@/lib/cn";
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
            <button
              type="button"
              onClick={() => setEditing("new")}
              className={cn(
                "mt-(--space-3) h-10 rounded-(--radius-md) px-(--space-4)",
                "bg-(--color-accent) text-white font-medium hover:bg-(--color-accent-strong)",
              )}
            >
              {t("targetTab.customListEmptyCta")}
            </button>
          </div>
        ) : (
          <>
            <ul className="flex flex-col gap-(--space-2)">
              {customs.map((c) => (
                <CustomRow key={c.id} target={c} onEdit={() => setEditing(c)} />
              ))}
            </ul>
            <button
              type="button"
              onClick={() => setEditing("new")}
              className={cn(
                "self-start h-10 rounded-(--radius-md) px-(--space-4) font-medium text-white",
                "bg-(--color-accent) hover:bg-(--color-accent-strong)",
              )}
            >
              {t("targetTab.addCustomTarget")}
            </button>
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
      <button
        type="button"
        onClick={onEdit}
        className="text-(length:--text-sm) text-(--color-accent) hover:underline"
      >
        {t("security.customRowEdit")}
      </button>
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
      label: existing?.label ?? "Custom target",
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
            <button
              type="button"
              onClick={() => void onDelete()}
              className="h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) text-(--color-error) hover:bg-(--color-error-faint)"
            >
              {t("targetTab.deleteTarget")}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) text-(--color-fg-default) hover:bg-(--color-bg-sunken)"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={() => void onSave()}
            disabled={form.formState.isSubmitting}
            className={cn(
              "h-10 rounded-(--radius-md) px-(--space-4) font-medium text-white",
              "bg-(--color-accent) hover:bg-(--color-accent-strong)",
              form.formState.isSubmitting && "opacity-50 cursor-not-allowed",
            )}
          >
            {t("settings.save")}
          </button>
        </>
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
          {t("targetTab.urlCustomInput")}
        </span>
        <input
          type="text"
          placeholder="rtmps://example.com/app"
          {...form.register("url", { required: true, maxLength: 2048 })}
          className={cn(
            "mt-(--space-1) h-10 w-full rounded-(--radius-md) border bg-(--color-bg-base)",
            "border-(--color-border-subtle) px-(--space-3)",
            "font-mono text-(length:--text-sm) text-(--color-fg-strong)",
            "focus:border-(--color-accent) focus:outline-none",
          )}
        />
      </label>
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
      <label className="block">
        <span className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
          {t("targetTab.streamKeySection")}
        </span>
        <div className="mt-(--space-1)">
          <SecretField
            variant="entry"
            value={form.watch("streamKey")}
            onChange={(v) => form.setValue("streamKey", v, { shouldDirty: true })}
            ariaLabel={t("targetTab.streamKeySection")}
          />
        </div>
      </label>
    </SettingsSection>
  );
}
