import { useEffect, useRef, useState, type ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";

import { Button } from "@/components/Button";
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
            "z-50 max-w-(--width-popover) rounded-(--radius-lg) border bg-(--color-bg-elevated)",
            "border-(--color-border-subtle) p-(--space-4) shadow-(--shadow-popover)",
          )}
        >
          <div className="text-(length:--text-sm) text-(--color-fg-strong)">
            {body}
          </div>
          {/* Slice-6 SA-BLOCK-2: inline button-bypass sites replaced by
              Button primitive. Confirm button uses the canonical danger
              variant so the slice-6 UI-CHECKPOINT-2 white-on-red fix
              propagates here automatically. */}
          <div className="mt-(--space-4) flex justify-end gap-(--space-2)">
            <Button
              variant="ghost"
              size="md"
              onClick={() => setOpen(false)}
            >
              {cancelLabel}
            </Button>
            <Button
              variant="danger"
              size="md"
              disabled={disabled}
              onClick={() => {
                setOpen(false);
                onConfirm();
              }}
            >
              {confirmLabel}
            </Button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
