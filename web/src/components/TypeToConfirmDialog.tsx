import { useEffect, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { WarningCircle } from "@phosphor-icons/react";

import { Button } from "./Button";
import { Input } from "./Input";
import { cn } from "@/lib/cn";
import { t } from "@/messages";

export interface TypeToConfirmDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly title: string;
  readonly body: ReactNode;
  readonly cannotUndo?: ReactNode;
  readonly phrase: string;
  readonly confirmLabel: string;
  readonly onConfirm: () => void;
  readonly destructive?: boolean;
}

/**
 * Type-to-confirm dialog (Radix Dialog modal). Design-system §6.13 +
 * phase-9-design-memo §D3.b.
 *
 * Confirm button disabled until typed === phrase exactly (case-sensitive).
 * Dialog-scoped `aria-live="polite"` region announces "X of N characters
 * match" with 500 ms throttle.
 */
export function TypeToConfirmDialog(
  props: TypeToConfirmDialogProps,
): ReactNode {
  const {
    open,
    onOpenChange,
    title,
    body,
    cannotUndo,
    phrase,
    confirmLabel,
    onConfirm,
    destructive = true,
  } = props;
  const [typed, setTyped] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const lastAnnounceRef = useRef<number>(0);
  const matched = matchingPrefixLen(typed, phrase);
  const isMatch = typed === phrase;

  useEffect(() => {
    if (!open) {
      setTyped("");
      setAnnouncement("");
      lastAnnounceRef.current = 0;
    }
  }, [open]);

  useEffect(() => {
    const now = performance.now();
    if (now - lastAnnounceRef.current < 500 && !isMatch) return;
    lastAnnounceRef.current = now;
    if (isMatch) {
      setAnnouncement(t("confirm.matchOk"));
    } else {
      setAnnouncement(
        t("confirm.matchProgress", { matched, total: phrase.length }),
      );
    }
  }, [matched, isMatch, phrase.length]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-(--color-bg-overlay)" />
        <Dialog.Content
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            // Focus the input — UX Designer §D3.b override.
            const el = document.getElementById("ttc-input");
            if (el) (el as HTMLInputElement).focus();
          }}
          className={cn(
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
            "w-[90vw] max-w-(--width-dialog-lg) rounded-(--radius-lg) border",
            "bg-(--color-bg-base) border-(--color-border-subtle) p-(--space-6)",
            "shadow-(--shadow-lg)",
          )}
        >
          <div className="flex items-start gap-(--space-3)">
            <WarningCircle
              className="mt-1 h-6 w-6 shrink-0 text-(--color-warn)"
              weight="regular"
              aria-hidden="true"
            />
            <Dialog.Title className="text-(length:--text-xl) font-semibold text-(--color-fg-strong)">
              {title}
            </Dialog.Title>
          </div>
          <Dialog.Description asChild>
            <div className="mt-(--space-3) text-(length:--text-sm) text-(--color-fg-default)">
              {body}
            </div>
          </Dialog.Description>
          {cannotUndo && (
            <div
              className={cn(
                "mt-(--space-3) rounded-(--radius-md) border px-(--space-3) py-(--space-2)",
                "border-(--color-warn) bg-(--color-warn-faint) text-(length:--text-xs) text-(--color-fg-strong)",
              )}
            >
              {cannotUndo}
            </div>
          )}
          <div className="mt-(--space-4)">
            <label
              htmlFor="ttc-input"
              className="block text-(length:--text-sm) font-medium text-(--color-fg-strong)"
            >
              {t("confirm.typeToConfirm", { phrase })}
            </label>
            <Input
              id="ttc-input"
              type="text"
              mono
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="mt-(--space-1)"
            />
            <div
              aria-live="polite"
              className="mt-(--space-1) sr-only"
            >
              {announcement}
            </div>
          </div>
          <div className="mt-(--space-5) flex justify-end gap-(--space-3)">
            <Button variant="ghost" size="md" onClick={() => onOpenChange(false)}>
              {t("confirm.cancel")}
            </Button>
            <Button
              variant={destructive ? "danger" : "primary"}
              size="md"
              disabled={!isMatch}
              onClick={() => {
                onConfirm();
                onOpenChange(false);
              }}
            >
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function matchingPrefixLen(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}
