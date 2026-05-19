import { useEffect, useState, type ReactNode } from "react";

import { CopyToClipboard } from "@/components/CopyToClipboard";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useAbout } from "@/hooks/useAbout";
import { t } from "@/messages";

export function AboutTab(): ReactNode {
  const { data, isPending } = useAbout();
  if (isPending || !data) {
    return (
      <div className="text-(length:--text-sm) text-(--color-fg-muted)">
        {t("common.loading")}
      </div>
    );
  }
  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {t("settings.pageAboutTitle")}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {t("settings.pageAboutSubtitle")}
      </p>
      <SettingsSection title={t("aboutTab.buildSection")}>
        <KV label={t("aboutTab.versionLabel")} value={data.version} />
        <KV
          label={t("aboutTab.buildShaLabel")}
          value={data.build_sha}
          copy
        />
      </SettingsSection>
      <SettingsSection title={t("aboutTab.uptimeSection")}>
        <UptimeTicker startedAt={data.started_at} />
        <div>
          <p className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
            {t("aboutTab.lastRebootsLabel")}
          </p>
          <ul className="mt-(--space-1) flex flex-col gap-(--space-1) text-(length:--text-sm) text-(--color-fg-muted) tabular-nums">
            {data.last_reboots.map((iso, i) => (
              <li key={`${iso}-${i}`}>{new Date(iso).toLocaleString()}</li>
            ))}
          </ul>
        </div>
      </SettingsSection>
      <SettingsSection
        title={t("aboutTab.logsSection")}
        intro={t("aboutTab.logsHelper")}
      >
        <div className="flex items-center gap-(--space-2)">
          <code className="flex-1 truncate rounded-(--radius-md) border border-(--color-border-subtle) bg-(--color-bg-sunken) px-(--space-3) py-(--space-2) font-mono text-(length:--text-sm) text-(--color-fg-strong)">
            {t("aboutTab.logsPath")}
          </code>
          <CopyToClipboard value={t("aboutTab.logsPath")} />
        </div>
      </SettingsSection>
      <SettingsSection title={t("aboutTab.licenseSection")}>
        <p className="text-(length:--text-sm) text-(--color-fg-strong)">
          {t("aboutTab.licenseLabel")}
        </p>
      </SettingsSection>
    </div>
  );
}

function KV({
  label,
  value,
  copy,
}: {
  readonly label: string;
  readonly value: string;
  readonly copy?: boolean;
}): ReactNode {
  return (
    <div>
      <span className="block text-(length:--text-sm) font-medium text-(--color-fg-strong)">
        {label}
      </span>
      <div className="mt-(--space-1) flex items-center gap-(--space-2)">
        <code className="font-mono text-(length:--text-sm) text-(--color-fg-strong)">
          {value}
        </code>
        {copy && <CopyToClipboard value={value} />}
      </div>
    </div>
  );
}

function formatUptime(d: number, h: number, m: number, s: number): string {
  return `${d}d ${h}h ${m}m ${s}s`;
}

function UptimeTicker({ startedAt }: { readonly startedAt: string }): ReactNode {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const ms = Math.max(0, now - new Date(startedAt).getTime());
  const sec = Math.floor(ms / 1000);
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return (
    <div>
      <span className="block text-(length:--text-sm) font-medium text-(--color-fg-strong)">
        {t("aboutTab.uptimeLabel")}
      </span>
      <p className="mt-(--space-1) font-mono text-(length:--text-sm) text-(--color-fg-strong) tabular-nums">
        {formatUptime(d, h, m, s)}
      </p>
    </div>
  );
}
