import { type ReactNode } from "react";
import * as RadixSlider from "@radix-ui/react-slider";

import { cn } from "@/lib/cn";

export interface SliderProps {
  readonly value: number;
  readonly onValueChange: (v: number) => void;
  readonly min: number;
  readonly max: number;
  readonly step?: number;
  readonly ariaLabel: string;
  readonly valueText: string;
  readonly disabled?: boolean;
  readonly displayValue?: ReactNode;
}

/** Design-system §6.18 + phase-9-design-memo §D5. */
export function Slider(props: SliderProps): ReactNode {
  const {
    value,
    onValueChange,
    min,
    max,
    step = 1,
    ariaLabel,
    valueText,
    disabled = false,
    displayValue,
  } = props;
  return (
    <div className="flex items-center gap-(--space-4)">
      <RadixSlider.Root
        className="relative flex h-6 flex-1 items-center"
        min={min}
        max={max}
        step={step}
        value={[value]}
        onValueChange={(vs) => {
          const v = vs[0];
          if (typeof v === "number") onValueChange(v);
        }}
        disabled={disabled}
        aria-label={ariaLabel}
      >
        <RadixSlider.Track className="relative h-1 flex-1 rounded-full bg-(--color-bg-sunken)">
          <RadixSlider.Range className="absolute h-full rounded-full bg-(--color-accent)" />
        </RadixSlider.Track>
        <RadixSlider.Thumb
          className={cn(
            "block h-6 w-6 rounded-full border-2 border-(--color-accent) bg-(--color-bg-base)",
            "shadow-(--shadow-sm) transition-transform hover:scale-[1.08] hover:shadow-(--shadow-md)",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-(--color-accent) focus-visible:ring-offset-2",
            disabled && "cursor-not-allowed border-(--color-fg-faint)",
          )}
          aria-valuetext={valueText}
        />
      </RadixSlider.Root>
      <div
        className={cn(
          "w-20 text-right font-mono text-(length:--text-sm) text-(--color-fg-strong)",
          "tabular-nums",
        )}
      >
        {displayValue ?? value}
      </div>
    </div>
  );
}
