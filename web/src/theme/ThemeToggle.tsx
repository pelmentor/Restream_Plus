import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Monitor, Moon, Sun } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { t } from "@/messages";
import { themeManager, type ThemeChoice } from "./ThemeManager";

interface SegmentDef {
  value: ThemeChoice;
  label: string;
  aria: string;
  Icon: typeof Sun;
}

const SEGMENTS: readonly SegmentDef[] = [
  { value: "light",  label: t("theme.light"),  aria: t("theme.lightAria"),  Icon: Sun },
  { value: "dark",   label: t("theme.dark"),   aria: t("theme.darkAria"),   Icon: Moon },
  { value: "system", label: t("theme.system"), aria: t("theme.systemAria"), Icon: Monitor },
];

export function ThemeToggle(): ReactNode {
  const [choice, setChoice] = useState<ThemeChoice>(() => themeManager.getState().choice);
  const reducedMotion = useReducedMotion();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const unsubscribe = themeManager.subscribe((s) => setChoice(s.choice));
    return unsubscribe;
  }, []);

  const pick = useCallback((value: ThemeChoice) => {
    themeManager.set(value);
  }, []);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const dir = event.key === "ArrowRight" ? 1 : -1;
      const next = (index + dir + SEGMENTS.length) % SEGMENTS.length;
      const seg = SEGMENTS[next];
      if (seg === undefined) return;
      refs.current[next]?.focus();
      pick(seg.value);
    },
    [pick],
  );

  return (
    <div
      role="radiogroup"
      aria-label={t("theme.label")}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-(--color-border-subtle)",
        "bg-(--color-bg-elevated) p-1",
      )}
    >
      {SEGMENTS.map((seg, index) => {
        const checked = choice === seg.value;
        return (
          <button
            key={seg.value}
            ref={(el) => { refs.current[index] = el; }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={seg.aria}
            tabIndex={checked ? 0 : -1}
            data-state={checked ? "checked" : "unchecked"}
            onClick={() => pick(seg.value)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium",
              reducedMotion ? "transition-none" : "transition-colors duration-150 ease-out",
              checked
                ? "bg-(--color-bg-base) text-(--color-fg-strong) shadow-(--shadow-xs)"
                : "text-(--color-fg-muted) hover:bg-(--color-bg-sunken) hover:text-(--color-fg-default)",
            )}
          >
            <seg.Icon className="h-4 w-4" weight="regular" aria-hidden="true" />
            <span>{seg.label}</span>
          </button>
        );
      })}
    </div>
  );
}
