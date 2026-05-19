import type { ReactNode } from "react";
import type { Icon as PhosphorIcon } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";

export interface EmptyStateProps {
  icon?: PhosphorIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps): ReactNode {
  return (
    <div
      className={cn(
        // Hex Audit UX-F12 (slice 10): `gap-(--space-4)` token instead
        // of raw `gap-4`. Aligns with the spacing-scale single-source-
        // of-truth invariant established in slice 5 (UI-F8 / UX-F8).
        "flex flex-col items-center justify-center gap-(--space-4) text-center",
        "px-(--space-4) py-(--space-16)",
        className,
      )}
    >
      {Icon !== undefined && (
        <Icon className="h-12 w-12 text-(--color-fg-muted)" weight="regular" aria-hidden="true" />
      )}
      <h2 className="text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">{title}</h2>
      {description !== undefined && (
        <p className="max-w-prose text-(--color-fg-muted)">{description}</p>
      )}
      {action !== undefined && <div className="mt-(--space-2)">{action}</div>}
    </div>
  );
}
