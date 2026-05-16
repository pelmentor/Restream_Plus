import { useEffect, useRef, useState, type ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";

import { cn } from "@/lib/cn";
import { t } from "@/messages";

export interface DestructiveConfirmProps {
  readonly trigger: ReactNode;
  readonly body: ReactNode;
  readonly confirmLabel: string;
  readonly cancelLabel?: string;
  readonly onConfirm: () => void;
  readonly disabled?: boolean;
}

/**
 * Inline popconfirm — Radix Popover anchored to the trigger.
 * Design-system §6.13 (popconfirm flavor) + phase-9-design-memo §D3.a.
 *
 * Outside-click, Escape, or 8 s inactivity → close. Focus returns to
 * the trigger.
 */
export function DestructiveConfirm(props: DestructiveConfirmProps): ReactNode {
  const {
    trigger,
    body,
    confirmLabel,
    cancelLabel = t("confirm.cancel"),
    onConfirm,
    disabled = false,
  } = props;
  const [open, setOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!open) {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      return;
    }
    timerRef.current = setTimeout(() => setOpen(false), 8000);
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, [open]);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>{trigger}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="bottom"
          align="end"
          sideOffset={6}
          className={cn(
            "z-50 max-w-[280px] rounded-(--radius-lg) border bg-(--color-bg-elevated)",
            "border-(--color-border-subtle) p-(--space-4) shadow-(--shadow-popover)",
          )}
        >
          <div className="text-(length:--text-sm) text-(--color-fg-strong)">
            {body}
          </div>
          <div className="mt-(--space-4) flex justify-end gap-(--space-2)">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className={cn(
                "h-8 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm)",
                "text-(--color-fg-default) hover:bg-(--color-bg-sunken)",
              )}
            >
              {cancelLabel}
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                setOpen(false);
                onConfirm();
              }}
              className={cn(
                "h-8 rounded-(--radius-md) px-(--space-3) text-(length:--text-sm)",
                "bg-(--color-error) text-white font-medium",
                "hover:bg-(--color-error)/85",
                disabled && "opacity-50 cursor-not-allowed",
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
