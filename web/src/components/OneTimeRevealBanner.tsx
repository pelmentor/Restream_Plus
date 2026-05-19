import { useEffect, useRef, type ReactNode } from "react";
import { Warning } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";
import { t } from "@/messages";

import { CopyToClipboard } from "./CopyToClipboard";
import { SecretField } from "./SecretField";

export interface OneTimeRevealBannerProps {
  readonly title: string;
  readonly body?: ReactNode;
  readonly value: string;
  readonly onDismiss: () => void;
  readonly ariaLabel: string;
}

/**
 * Design-system §6.14 + phase-9-design-memo §D4. Yellow-tinted persistent
 * banner with embedded SecretField (entry, pre-revealed) + CopyToClipboard
 * + dismiss. Focus moves to the CopyToClipboard button on mount.
 */
export function OneTimeRevealBanner(
  props: OneTimeRevealBannerProps,
): ReactNode {
  const { title, body, value, onDismiss, ariaLabel } = props;
  const copyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const btn = copyRef.current?.querySelector("button");
    if (btn instanceof HTMLButtonElement) btn.focus();
  }, []);

  return (
    <section
      aria-labelledby="otr-title"
      className={cn(
        "rounded-(--radius-lg) border bg-(--color-warn-faint)",
        "border-(--color-warn) border-l-4",
        "p-(--space-5) mb-(--space-6)",
      )}
    >
      <div className="flex items-start gap-(--space-3)">
        <Warning
          className="mt-1 h-6 w-6 shrink-0 text-(--color-warn)"
          weight="regular"
          aria-hidden="true"
        />
        <div className="flex-1">
          <h3
            id="otr-title"
            className="text-(length:--text-lg) font-semibold text-(--color-fg-strong)"
          >
            {title}
          </h3>
          {body !== undefined && (
            <p className="mt-(--space-1) text-(length:--text-sm) text-(--color-fg-default)">
              {body}
            </p>
          )}
        </div>
      </div>
      <div className="mt-(--space-4) flex items-center gap-(--space-2)">
        <div className="flex-1">
          <SecretField
            variant="entry"
            value={value}
            onChange={() => {
              /* read-only — the value is server-generated, edits are no-ops */
            }}
            ariaLabel={ariaLabel}
            initiallyRevealed
            autoComplete="off"
          />
        </div>
        <div ref={copyRef}>
          <CopyToClipboard value={value} />
        </div>
      </div>
      <div className="mt-(--space-4) flex justify-end">
        <button
          type="button"
          onClick={onDismiss}
          className={cn(
            "h-(--size-control-md) rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium",
            "bg-(--color-accent) text-(--color-on-accent) hover:bg-(--color-accent-strong)",
          )}
        >
          {t("reveal.bannerDismiss")}
        </button>
      </div>
    </section>
  );
}
