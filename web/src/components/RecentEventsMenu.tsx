import { useEffect, useRef, useState, type ReactNode } from "react";
import { Bell, BellSlash } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";
import { t } from "@/messages";
import {
  useRecentEvents,
  useRecentEventsActions,
  type RecentEvent,
} from "./RecentEventsProvider";

/**
 * Design-system §6.21 + ux-flows §1: bell with unseen-count dot;
 * 320px dropdown listing last 50 in-session events. NOT persisted.
 * Events are pushed by `StatusStreamHost` per phase-8-design-memo §O.
 */
export function RecentEventsMenu(): ReactNode {
  const { events, unseenCount } = useRecentEvents();
  const { markSeen, clear } = useRecentEventsActions();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    markSeen();
    const onClick = (e: MouseEvent): void => {
      const target = e.target;
      if (!(target instanceof Node)) return;
      if (containerRef.current !== null && !containerRef.current.contains(target)) {
        setOpen(false);
      }
    };
    // Reviewer L-2: keyboard escape for the disclosure (ARIA APG).
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, markSeen]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("recentEvents.trigger")}
        aria-expanded={open}
        aria-haspopup="menu"
        className="relative inline-flex h-9 w-9 items-center justify-center rounded-full border border-(--color-border-subtle) bg-(--color-bg-elevated) hover:bg-(--color-bg-sunken)"
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
      </button>
      {open && (
        <div
          className={cn(
            "absolute right-0 mt-2 w-80 max-h-[480px]",
            "rounded-(--radius-md) border border-(--color-border-subtle)",
            "bg-(--color-bg-base) shadow-(--shadow-md)",
            "flex flex-col overflow-hidden",
          )}
        >
          <div className="flex-1 overflow-y-auto">
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-(--space-2) py-(--space-8) text-center text-(--color-fg-muted)">
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
        </div>
      )}
    </div>
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
