import { useEffect, useState, type ReactNode } from "react";
import { Bell, BellSlash } from "@phosphor-icons/react";
import * as Popover from "@radix-ui/react-popover";

import { Button } from "@/components/Button";
import { cn } from "@/lib/cn";
import { t } from "@/messages";
import {
  useRecentEvents,
  useRecentEventsActions,
  type RecentEvent,
} from "./RecentEventsProvider";

/**
 * Design-system §6.21 + ux-flows §1: bell with unseen-count dot;
 * 320px popover listing last 50 in-session events. NOT persisted.
 * Events are pushed by `StatusStreamHost` per phase-8-design-memo §O.
 *
 * Hex Audit slice-6 deferral (closed in slice 10): migrated from a
 * hand-rolled disclosure (manual outside-click + Escape listeners +
 * containerRef) to **Radix Popover**. Why Popover, not DropdownMenu:
 * the popup is a `dialog`-shape panel (list of event rows + a single
 * "Clear" action), NOT a `role="menu"` container with `menuitem`
 * keyboard navigation. Radix Popover handles outside-click, Escape,
 * focus return to trigger, portal mounting, and aria-haspopup="dialog"
 * automatically — the pre-slice-10 hand-rolled version reimplemented
 * each badly (the outside-click listener fired on `mousedown` AND
 * touched the bell-button's own area via a `containerRef` workaround;
 * the Escape listener was global; focus didn't return to the trigger).
 */
export function RecentEventsMenu(): ReactNode {
  const { events, unseenCount } = useRecentEvents();
  const { markSeen, clear } = useRecentEventsActions();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (open) markSeen();
  }, [open, markSeen]);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        {/* Slice-6 UI-F4: trigger grows to size=lg (44×44) for WCAG 2.5.5
            touch-target floor. `rounded-full` retained — bell-icon header
            chip universal affordance. */}
        <Button
          iconOnly
          variant="secondary"
          size="lg"
          aria-label={t("recentEvents.trigger")}
          className="relative rounded-full"
        >
          <Bell
            className="h-5 w-5 text-(--color-fg-default)"
            weight="regular"
            aria-hidden="true"
          />
          {unseenCount > 0 && (
            <span
              className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-(--color-error)"
              style={{ boxShadow: "0 0 0 2px var(--color-bg-base)" }}
              aria-hidden="true"
            />
          )}
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={8}
          className={cn(
            "w-80 max-h-(--height-events-menu)",
            "rounded-(--radius-md) border border-(--color-border-subtle)",
            "bg-(--color-bg-base) shadow-(--shadow-md)",
            "flex flex-col overflow-hidden",
            "z-50",
          )}
        >
          <div className="flex-1 overflow-y-auto">
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-(--space-2) py-(--space-8) text-center text-(--color-fg-muted)">
                {/* Empty-state illustration, not a control — 32px per
                    design-system §6.16, exempt from UI-F4 floor. */}
                <BellSlash
                  className="h-8 w-8"
                  weight="regular"
                  aria-hidden="true"
                />
                <span>{t("recentEvents.empty")}</span>
              </div>
            ) : (
              events.map((evt) => <EventRow key={evt.id} event={evt} />)
            )}
          </div>
          <button
            type="button"
            onClick={() => {
              clear();
              setOpen(false);
            }}
            className="block w-full text-center py-(--space-3) text-(length:--text-sm) text-(--color-accent) hover:bg-(--color-bg-elevated) border-t border-(--color-border-subtle)"
          >
            {t("recentEvents.clear")}
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function EventRow({ event }: { readonly event: RecentEvent }): ReactNode {
  const dotColor =
    event.kind === "error"
      ? "bg-(--color-error)"
      : event.kind === "warn"
        ? "bg-(--color-warn)"
        : event.kind === "success"
          ? "bg-(--color-live)"
          : "bg-(--color-fg-muted)";
  return (
    <div className="flex items-start gap-(--space-2) px-(--space-3) py-(--space-2) border-b border-(--color-border-subtle) hover:bg-(--color-bg-elevated)">
      <span
        className={cn("h-1.5 w-1.5 mt-1.5 rounded-full shrink-0", dotColor)}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        <div className="text-(length:--text-2xs) text-(--color-fg-muted) tabular-nums">
          {event.at.toLocaleTimeString()}
        </div>
        <div className="text-(length:--text-sm) text-(--color-fg-default)">
          {event.message}
        </div>
      </div>
    </div>
  );
}
