import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Copy, WarningCircle } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";
import { t } from "@/messages";

type State = "idle" | "copying" | "copied" | "failed";

export interface CopyToClipboardProps {
  readonly value: string;
  readonly label?: string;
}

/**
 * Design-system §6.11 + phase-9-design-memo §D2.
 *
 * idle → copying → copied (1500 ms) → idle
 *                 └fail──▶ failed (3000 ms) ─▶ idle
 *
 * Inline `<span aria-live="polite" class="sr-only">` per instance for
 * the local "Copied" / "Copy failed" announcement — the one allowed
 * exception to "page-scoped only" (phase-9-design-memo §E note 1.iv).
 */
export function CopyToClipboard(props: CopyToClipboardProps): ReactNode {
  const { value, label } = props;
  const [state, setState] = useState<State>("idle");
  const [announcement, setAnnouncement] = useState("");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  const onClick = async (): Promise<void> => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    setState("copying");
    try {
      if (
        typeof navigator !== "undefined" &&
        typeof navigator.clipboard?.writeText === "function" &&
        window.isSecureContext
      ) {
        await navigator.clipboard.writeText(value);
      } else {
        throw new Error("clipboard unavailable");
      }
      setState("copied");
      setAnnouncement(t("copy.announceCopied"));
      timerRef.current = setTimeout(() => {
        setState("idle");
        setAnnouncement("");
      }, 1500);
    } catch {
      setState("failed");
      setAnnouncement(t("copy.announceFailed"));
      timerRef.current = setTimeout(() => {
        setState("idle");
        setAnnouncement("");
      }, 3000);
    }
  };

  const text =
    state === "idle"
      ? (label ?? t("copy.idle"))
      : state === "copying"
        ? t("copy.copying")
        : state === "copied"
          ? t("copy.copied")
          : t("copy.failed");
  const Icon =
    state === "copied" ? Check : state === "failed" ? WarningCircle : Copy;
  const color =
    state === "copied"
      ? "text-(--color-live)"
      : state === "failed"
        ? "text-(--color-error)"
        : "text-(--color-fg-muted) hover:text-(--color-accent)";

  return (
    <>
      <button
        type="button"
        onClick={() => void onClick()}
        aria-label={label ?? t("copy.idle")}
        className={cn(
          "inline-flex items-center gap-(--space-1) rounded-(--radius-md)",
          "px-(--space-2) text-(length:--text-xs)",
          // Slice-5: both variants land on control-md per UX-architect
          // "drift sites = control-md by default" rule. Pre-slice-5 the
          // inline variant was h-8 (32px) which sat between control-sm
          // and control-md inconsistently.
          "h-(--size-control-md)",
          "hover:bg-(--color-bg-elevated)",
          color,
        )}
      >
        <Icon className="h-4 w-4" weight="regular" aria-hidden="true" />
        <span>{text}</span>
      </button>
      <span aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </>
  );
}
