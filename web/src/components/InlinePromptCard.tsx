import { useMemo, useState, type ReactNode } from "react";

import { Button } from "./Button";
import { Input } from "./Input";
import { t } from "@/messages";

import type { TargetT } from "@/lib/schemas/targets";

export interface InlinePromptCardProps {
  readonly vkTargets: readonly TargetT[];
  readonly submitting: boolean;
  readonly onSave: (perTarget: ReadonlyMap<string, string>) => void;
  readonly onSkip: () => void;
}

/**
 * Design-system §6.15 + ux-flows §2 VK flow + F# UX-B.multi-vk.
 *
 * One paste-only input per enabled VK target lacking a credential.
 * Passive validation hint (informational, not blocking). Buttons
 * `lg` not `xl` — hero owns `xl` (F# UI-C.start-width).
 *
 * Phase 8 minimal SecretField — Phase 9's full SecretField component
 * (with reveal-mask transitions) is unnecessary here: the input is
 * single-use paste, never recalled.
 */
export function InlinePromptCard({
  vkTargets,
  submitting,
  onSave,
  onSkip,
}: InlinePromptCardProps): ReactNode {
  const [values, setValues] = useState<Record<string, string>>({});
  const allFilled = useMemo(
    () => vkTargets.every((t) => (values[t.id] ?? "").length > 0),
    [values, vkTargets],
  );

  function update(id: string, value: string): void {
    setValues((prev) => ({ ...prev, [id]: value }));
  }

  function submit(): void {
    const map = new Map<string, string>();
    vkTargets.forEach((t) => {
      const v = values[t.id];
      if (v !== undefined && v.length > 0) map.set(t.id, v);
    });
    onSave(map);
  }

  return (
    <div className="mt-(--space-6) rounded-(--radius-lg) bg-(--color-warn-faint) border-l-4 border-(--color-warn) p-(--space-6) flex flex-col gap-(--space-4)">
      <h3 className="text-(length:--text-lg) font-semibold text-(--color-fg-strong)">
        {t("dashboard.vkPromptTitle")}
      </h3>
      <p className="text-(length:--text-sm) text-(--color-fg-default)">
        {t("dashboard.vkPromptIntro")}
      </p>

      <div className="flex flex-col gap-(--space-3)">
        {vkTargets.map((target) => {
          const value = values[target.id] ?? "";
          const hint = classifyKey(value);
          return (
            <label key={target.id} className="flex flex-col gap-(--space-1)">
              <span className="text-(length:--text-xs) font-medium text-(--color-fg-strong)">
                {target.label}
              </span>
              <Input
                type="password"
                size="lg"
                mono
                // Reviewer M-1: `autocomplete="off"` is ignored by Chrome/
                // Firefox on password fields; `new-password` is the
                // documented way to suppress autofill on a paste-only
                // field whose value must never persist.
                autoComplete="new-password"
                spellCheck={false}
                value={value}
                onChange={(e) => update(target.id, e.target.value)}
              />
              <span className="text-(length:--text-xs) text-(--color-fg-muted)">
                {hint === "empty"
                  ? ""
                  : hint === "valid"
                    ? t("dashboard.vkLooksValid")
                    : t("dashboard.vkLooksMalformed")}
              </span>
            </label>
          );
        })}
      </div>

      <div className="flex items-center justify-end gap-(--space-3)">
        <Button variant="ghost" size="lg" onClick={onSkip} disabled={submitting}>
          {t("dashboard.vkSkip")}
        </Button>
        <Button
          variant="primary"
          size="lg"
          onClick={submit}
          disabled={!allFilled || submitting}
          loading={submitting}
        >
          {t("dashboard.vkSave")}
        </Button>
      </div>
    </div>
  );
}

function classifyKey(value: string): "empty" | "valid" | "malformed" {
  if (value.length === 0) return "empty";
  // Passive heuristic: VK keys are typically 20+ chars, base-something.
  if (value.length < 16 || /\s/.test(value)) return "malformed";
  return "valid";
}
