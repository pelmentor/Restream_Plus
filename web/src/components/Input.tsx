import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

import { cn } from "@/lib/cn";

export type InputSize = "sm" | "md" | "lg";

// Slice-4 primitive (closes UX-F5, UI-F6, UI-F7). Companion to `Button`.
//
// Heights map 1:1 to Button (`h-7 / h-9 / h-11`) so Input + Button on the
// same row share a baseline — see UI-designer memo §3, the most-violated
// alignment in the pre-slice-4 codebase.
//
// `focus-visible` deliberately does NOT set `outline-none`. The 2 px
// accent outline at tokens.css:185-189 (`*:focus-visible`) is the global
// a11y contract; suppressing it is exactly UI-F7. Input only swaps the
// border colour to accent — the outline does the rest.
//
// Slice-5 re-audit FH2-H3: DOM shape changes with slot props.
//   - No slot:          returns a bare `<input>` element.
//   - leading/trailing: returns `<span class="relative block"><input/>
//                       <slot/></span>` wrapping the input.
// Callers passing `className` for INPUT styling (font, padding, height)
// work in both modes. Callers passing `className` for PARENT-layout
// behaviour (e.g., `className="flex-1"` on the input to stretch in a
// flex row) WILL BREAK when a slot is added: the input stretches but
// the wrapping `<span>` does not. Use `containerClassName` for
// parent-layout classes instead — it merges into the wrapper when one
// exists, and is silently ignored when no slot is present (which is
// the correct fallback: a bare input is already a flex child).

const baseClass =
  "block w-full rounded-md border bg-(--color-bg-base) " +
  "border-(--color-border-subtle) " +
  "text-(--color-fg-strong) placeholder:text-(--color-fg-faint) " +
  "transition-colors duration-150 ease-out " +
  "hover:border-(--color-border-strong) " +
  "focus-visible:border-(--color-accent) " +
  // Slice-5 UI-F1: disabled tokens replace `opacity-60` (was crushing
  // contrast to ~2:1). Explicit fg/bg/border tokens deliver ~5:1 in
  // both themes per UI-designer memo Q2.
  "disabled:bg-(--color-bg-disabled) disabled:text-(--color-fg-disabled) " +
  "disabled:border-(--color-border-disabled) disabled:cursor-not-allowed " +
  "read-only:bg-(--color-bg-sunken) read-only:text-(--color-fg-default) " +
  "aria-invalid:border-(--color-error) " +
  "aria-invalid:focus-visible:border-(--color-error)";

const sizeClasses: Record<InputSize, string> = {
  sm: "h-(--size-control-sm) px-(--space-2) text-(length:--text-xs)",
  md: "h-(--size-control-md) px-(--space-3) text-(length:--text-sm)",
  lg: "h-(--size-control-lg) px-(--space-4) text-(length:--text-base)",
};

// Padding overrides when a slot is present. The slot icon/button is
// absolutely positioned; the input itself loses its symmetric side
// padding and gains room for the slot.
const slotPaddingLeft: Record<InputSize, string> = {
  sm: "pl-(--space-6)",   // 1.5rem — leaves room for a 16px icon at left-2
  md: "pl-(--space-8)",   // 2rem
  lg: "pl-(--space-10)",  // 2.5rem
};
const slotPaddingRight: Record<InputSize, string> = {
  sm: "pr-(--space-6)",
  md: "pr-(--space-10)",  // 2.5rem — room for 36×36 trailing button
  lg: "pr-(--space-12)",  // 3rem — room for 44×44 trailing button
};

const slotLeftPosition: Record<InputSize, string> = {
  sm: "left-(--space-2)",
  md: "left-(--space-3)",
  lg: "left-(--space-3)",
};
const slotRightPosition: Record<InputSize, string> = {
  sm: "right-(--space-1)",
  md: "right-(--space-1)",
  lg: "right-(--space-1)",
};

// HTML's `size` attribute (character-width hint) collides with our
// visual size prop. Nothing in this codebase uses native `size`.
// `prefix` is omitted because TS DOM lib's stray global attribute
// conflicts with our slot naming.
export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "size" | "prefix"> {
  size?: InputSize;
  /** Sets `aria-invalid="true"` and the error-tinted border. */
  invalid?: boolean;
  /** Flip to `font-mono tabular-nums`. For credential / numeric fields. */
  mono?: boolean;
  /** Absolutely-positioned leading slot (e.g., search icon). */
  leading?: ReactNode;
  /**
   * Absolutely-positioned trailing slot. Wrapper does NOT set
   * `pointer-events-none` (callers typically pass an interactive
   * Button — e.g., SecretField's reveal toggle). For a non-interactive
   * trailing node (label, unit, decorative icon) the caller is
   * responsible for `pointer-events-none` on the inner element so it
   * doesn't shadow the input's hit area.
   */
  trailing?: ReactNode;
  /** Class merged into the relative wrapper when a slot is present. */
  containerClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    size = "md",
    invalid = false,
    mono = false,
    leading,
    trailing,
    className,
    containerClassName,
    type,
    ...rest
  },
  ref,
) {
  const hasLeading = leading !== undefined && leading !== null;
  const hasTrailing = trailing !== undefined && trailing !== null;
  const hasSlot = hasLeading || hasTrailing;

  const inputClass = cn(
    baseClass,
    sizeClasses[size],
    mono && "font-mono tabular-nums",
    hasLeading && slotPaddingLeft[size],
    hasTrailing && slotPaddingRight[size],
    className,
  );

  const inputEl = (
    <input
      ref={ref}
      type={type ?? "text"}
      aria-invalid={invalid || undefined}
      className={inputClass}
      {...rest}
    />
  );

  if (!hasSlot) return inputEl;

  return (
    <span className={cn("relative block", containerClassName)}>
      {hasLeading && (
        <span
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2",
            "text-(--color-fg-muted)",
            slotLeftPosition[size],
          )}
          aria-hidden="true"
        >
          {leading}
        </span>
      )}
      {inputEl}
      {hasTrailing && (
        <span
          className={cn(
            "absolute top-1/2 -translate-y-1/2",
            slotRightPosition[size],
          )}
        >
          {trailing}
        </span>
      )}
    </span>
  );
});
