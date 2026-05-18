import { type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import {
  ArrowLeft,
  ClockCounterClockwise,
  Info,
  ShieldCheck,
  Sliders,
} from "@phosphor-icons/react";

import { cn } from "@/lib/cn";
import { t } from "@/messages";

type TabKey =
  | "tabGeneral"
  | "tabSecurity"
  | "tabSessions"
  | "tabAbout"
  | "tabTargets"
  | "tabTwitch"
  | "tabYoutube"
  | "tabKick"
  | "tabVk"
  | "tabCustom";

// Absolute `to=` values only — see settings/index.tsx for the
// background on why relative routes here can compound segments
// onto the URL and crash the SPA fallback.
const NAV: readonly { to: string; label: TabKey; Icon: typeof Sliders }[] = [
  { to: "/settings/general", label: "tabGeneral", Icon: Sliders },
  // Targets is a section header rendered specially below.
  { to: "/settings/security", label: "tabSecurity", Icon: ShieldCheck },
  { to: "/settings/sessions", label: "tabSessions", Icon: ClockCounterClockwise },
  { to: "/settings/about", label: "tabAbout", Icon: Info },
];

const TARGET_TYPES: readonly { to: string; label: TabKey }[] = [
  { to: "/settings/targets/twitch", label: "tabTwitch" },
  { to: "/settings/targets/youtube", label: "tabYoutube" },
  { to: "/settings/targets/kick", label: "tabKick" },
  { to: "/settings/targets/vk", label: "tabVk" },
  { to: "/settings/targets/custom", label: "tabCustom" },
];

export function SettingsSidebar(): ReactNode {
  return (
    <aside
      className={cn(
        "sticky top-(--space-6) self-start",
        "max-h-[calc(100vh-var(--space-24))] overflow-y-auto",
        "rounded-(--radius-lg) border bg-(--color-bg-elevated)",
        "border-(--color-border-subtle) px-(--space-2) py-(--space-4)",
      )}
      aria-label={t("settings.sidebarHeading")}
    >
      <div
        className={cn(
          "mb-(--space-2) flex items-center gap-(--space-2) px-(--space-3) py-(--space-2)",
          "border-b border-(--color-border-subtle)",
        )}
      >
        <NavLink
          to="/"
          className={cn(
            "inline-flex items-center gap-(--space-1) text-(length:--text-sm)",
            "text-(--color-fg-muted) hover:text-(--color-fg-strong)",
          )}
        >
          <ArrowLeft className="h-4 w-4" weight="regular" aria-hidden="true" />
          <span>{t("settings.backToDashboard")}</span>
        </NavLink>
      </div>
      <nav>
        <ul className="flex flex-col gap-(--space-1)">
          {NAV.slice(0, 1).map((item) => (
            <SidebarLink key={item.to} {...item} />
          ))}
          <li
            className={cn(
              "px-(--space-3) pt-(--space-3) pb-(--space-1)",
              "text-(length:--text-2xs) font-semibold uppercase tracking-wide text-(--color-fg-muted)",
            )}
          >
            {t("settings.pageTargetsHeading")}
          </li>
          {TARGET_TYPES.map((item) => (
            <li key={item.to}>
              <SettingsNavLink to={item.to} indent>
                <span className="truncate">
                  { }
                  {labelFor(item.label)}
                </span>
              </SettingsNavLink>
            </li>
          ))}
          {NAV.slice(1).map((item) => (
            <SidebarLink key={item.to} {...item} />
          ))}
        </ul>
      </nav>
    </aside>
  );
}

function SidebarLink({
  to,
  label,
  Icon,
}: {
  readonly to: string;
  readonly label: TabKey;
  readonly Icon: typeof Sliders;
}): ReactNode {
  return (
    <li>
      <SettingsNavLink to={to}>
        <Icon className="h-5 w-5 shrink-0" weight="regular" aria-hidden="true" />
        <span className="truncate">{labelFor(label)}</span>
      </SettingsNavLink>
    </li>
  );
}

function SettingsNavLink({
  to,
  children,
  indent,
}: {
  readonly to: string;
  readonly children: ReactNode;
  readonly indent?: boolean;
}): ReactNode {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex h-10 items-center gap-(--space-3) rounded-(--radius-md) px-(--space-3)",
          "text-(length:--text-sm) font-medium text-(--color-fg-default)",
          "hover:bg-(--color-bg-sunken)",
          indent && "pl-(--space-6) h-9",
          isActive &&
            cn(
              "bg-(--color-accent-faint) text-(--color-accent) font-semibold",
              "[box-shadow:inset_3px_0_0_var(--color-accent)]",
            ),
        )
      }
      data-on-accent-faint
    >
      {children}
    </NavLink>
  );
}

function labelFor(key: TabKey): string {
  return t(`settings.${key}`);
}
