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
        "flex flex-col items-center justify-center gap-4 text-center",
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
