import type { ReactNode } from "react";
import { Info, Warning, WarningCircle } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";

export type BannerVariant = "info" | "warn" | "error";

const styles: Record<BannerVariant, string> = {
  info:
    "bg-(--color-info-faint) text-(--color-fg-strong) border-l-4 border-(--color-info)",
  warn:
    "bg-(--color-warn-faint) text-(--color-fg-strong) border-l-4 border-(--color-warn)",
  error:
    "bg-(--color-error-faint) text-(--color-fg-strong) border-l-4 border-(--color-error)",
};

const icons: Record<BannerVariant, typeof Info> = {
  info: Info,
  warn: Warning,
  error: WarningCircle,
};

export interface BannerProps {
  variant?: BannerVariant;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
  /**
   * Polite live-region announcement. Default for `error` is "assertive".
   */
  ariaLive?: "off" | "polite" | "assertive";
}

export function Banner({
  variant = "info",
  title,
  children,
  action,
  className,
  ariaLive,
}: BannerProps): ReactNode {
  const Icon = icons[variant];
  const live = ariaLive ?? (variant === "error" ? "assertive" : "polite");
  return (
    <div
      role="status"
      aria-live={live}
      className={cn(
        "flex items-start gap-(--space-3) rounded-md px-(--space-4) py-(--space-3) text-(length:--text-sm)",
        styles[variant],
        className,
      )}
    >
      <Icon className="mt-0.5 h-5 w-5 shrink-0" weight="regular" aria-hidden="true" />
      <div className="flex-1">
        {title !== undefined && (
          <div className="font-semibold text-(length:--text-sm)">{title}</div>
        )}
        {/* Slice-6 UI-CHECKPOINT-3: body steps down to --color-fg-default
            so the title (semibold + fg-strong) reads as the rank-1 line.
            Body still ≥10:1 on every *-faint bg in both themes. */}
        {children !== undefined && (
          <div className="text-(--color-fg-default)">{children}</div>
        )}
      </div>
      {action !== undefined && <div className="shrink-0">{action}</div>}
    </div>
  );
}
