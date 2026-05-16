import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface Metric {
  readonly label: string;
  readonly value: ReactNode;
  readonly unit?: string;
}

export interface MetricGridProps {
  readonly metrics: readonly Metric[];
  readonly columns?: 2 | 4;
  readonly className?: string;
}

/**
 * Design-system §6.19: label/value pairs in a 2- or 4-column grid.
 * Value uses --font-mono + tabular-nums for the number; units are
 * rendered alongside in muted --font-sans.
 */
export function MetricGrid({
  metrics,
  columns = 2,
  className,
}: MetricGridProps): ReactNode {
  return (
    <dl
      className={cn(
        "grid gap-x-(--space-4) gap-y-(--space-2)",
        columns === 4 ? "grid-cols-2 md:grid-cols-4" : "grid-cols-2",
        className,
      )}
    >
      {metrics.map((m) => (
        <div key={m.label} className="flex flex-col gap-(--space-1) min-w-0">
          <dt className="text-(length:--text-2xs) uppercase tracking-wider text-(--color-fg-muted) truncate">
            {m.label}
          </dt>
          <dd className="text-(length:--text-sm) font-(family-name:--font-mono) text-(--color-fg-strong) truncate tabular-nums">
            {m.value}
            {m.unit !== undefined && (
              <span className="ml-1 text-(--color-fg-muted) font-(family-name:--font-sans)">
                {m.unit}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
