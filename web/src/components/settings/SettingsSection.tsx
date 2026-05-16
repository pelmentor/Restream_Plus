import { type ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface SettingsSectionProps {
  readonly title: string;
  readonly intro?: ReactNode;
  readonly children: ReactNode;
  readonly footer?: ReactNode;
  readonly id?: string;
}

/**
 * The shared section-card pattern used inside every tab.
 * UI Designer §B.
 */
export function SettingsSection(props: SettingsSectionProps): ReactNode {
  const { title, intro, children, footer, id } = props;
  const slug = id ?? title.toLowerCase().replace(/\s+/g, "-");
  return (
    <section
      aria-labelledby={`sec-${slug}-title`}
      className={cn(
        "mb-(--space-6) overflow-hidden rounded-(--radius-lg) border bg-(--color-bg-elevated)",
        "border-(--color-border-subtle) shadow-(--shadow-xs)",
      )}
    >
      <header
        className={cn(
          "border-b border-(--color-border-subtle) px-(--space-6) pt-(--space-5) pb-(--space-4)",
        )}
      >
        <h2
          id={`sec-${slug}-title`}
          className="text-(length:--text-lg) font-semibold text-(--color-fg-strong)"
        >
          {title}
        </h2>
        {intro !== undefined && (
          <p className="mt-(--space-1) text-(length:--text-sm) text-(--color-fg-muted)">
            {intro}
          </p>
        )}
      </header>
      <div className="flex flex-col gap-(--space-5) p-(--space-6)">
        {children}
      </div>
      {footer !== undefined && (
        <footer
          className={cn(
            "border-t border-(--color-border-subtle) bg-(--color-bg-base)",
            "flex justify-end gap-(--space-3) px-(--space-6) py-(--space-4)",
          )}
        >
          {footer}
        </footer>
      )}
    </section>
  );
}
