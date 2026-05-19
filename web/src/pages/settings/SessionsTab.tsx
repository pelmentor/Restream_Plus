import { type ReactNode } from "react";

import { Button } from "@/components/Button";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useRunHistory } from "@/hooks/useRunHistory";
import { t } from "@/messages";

export function SessionsTab(): ReactNode {
  const q = useRunHistory();
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {t("settings.pageSessionsTitle")}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {t("settings.pageSessionsSubtitle")}
      </p>
      <SettingsSection title={t("settings.pageSessionsTitle")}>
        {q.isPending ? (
          <p className="text-(length:--text-sm) text-(--color-fg-muted)">
            {t("sessionsTab.loading")}
          </p>
        ) : items.length === 0 ? (
          <div className="rounded-(--radius-md) border border-dashed border-(--color-border-subtle) p-(--space-6) text-center">
            <p className="text-(length:--text-sm) text-(--color-fg-muted)">
              {t("sessionsTab.empty")}
            </p>
            <p className="mt-(--space-1) text-(length:--text-xs) text-(--color-fg-muted)">
              {t("sessionsTab.emptyHelper")}
            </p>
          </div>
        ) : (
          <>
            <table className="w-full text-(length:--text-sm)">
              <thead className="border-b border-(--color-border-subtle) text-(--color-fg-muted)">
                <tr>
                  <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">
                    {t("sessionsTab.columnStarted")}
                  </th>
                  <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">
                    {t("sessionsTab.columnDuration")}
                  </th>
                  <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">
                    {t("sessionsTab.columnEnded")}
                  </th>
                  <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">
                    {t("sessionsTab.columnReason")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id} className="border-b border-(--color-border-subtle)">
                    <td className="px-(--space-2) py-(--space-2) text-(--color-fg-strong) tabular-nums">
                      {new Date(row.started_at).toLocaleString()}
                    </td>
                    <td className="px-(--space-2) py-(--space-2) text-(--color-fg-muted) tabular-nums">
                      {row.duration_seconds === null
                        ? "—"
                        : `${Math.round(row.duration_seconds)}s`}
                    </td>
                    <td className="px-(--space-2) py-(--space-2) text-(--color-fg-muted) tabular-nums">
                      {row.ended_at === null ? "—" : new Date(row.ended_at).toLocaleString()}
                    </td>
                    <td className="px-(--space-2) py-(--space-2) text-(--color-fg-muted)">
                      {row.end_reason ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {q.hasNextPage && (
              <Button
                variant="secondary"
                size="md"
                className="mt-(--space-4) self-start"
                onClick={() => void q.fetchNextPage()}
                loading={q.isFetchingNextPage}
              >
                {t("sessionsTab.loadOlder")}
              </Button>
            )}
          </>
        )}
      </SettingsSection>
    </div>
  );
}
