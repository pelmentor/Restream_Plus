import {
  forwardRef,
  type SelectHTMLAttributes,
  type ReactNode,
} from "react";

import { cn } from "@/lib/cn";

export type SelectSize = "sm" | "md" | "lg";

export interface SelectOption {
  readonly value: string;
  readonly label: string;
  readonly disabled?: boolean;
}

// Slice-6 FH2-H1. Closes the raw `<select>` bypass at VKTab.tsx:111
// and PersistentTargetTab.tsx:174. Sits next to Button + Input as the
// third primitive of the slice-4/5/6 design-system rebuild.
//
// Native `<select>` (not Radix Select) because:
//   - Touch-platform pickers come for free (operator's emergency
//     fallback platform is phone — confirmed in project memory).
//   - Type-ahead and keyboard semantics are perfect.
//   - Zero JS bundle cost.
//
// API mirrors Input 1:1 for muscle-memory consistency. Per UX-architect
// memo §2.2: typed `options` prop, not children passthrough — Tailwind
// classes don't propagate into native `<option>` and the children form
// invites bypass.

// `prefix` and `size` are HTML attributes that collide with our props
// (visual size and slot semantics). Nothing in this codebase uses them.
export interface SelectProps
  extends Omit<
    SelectHTMLAttributes<HTMLSelectElement>,
    "size" | "prefix" | "children"
  > {
  readonly options: readonly SelectOption[];
  readonly size?: SelectSize;
  /** Sets `aria-invalid="true"` and the error-tinted border. */
  readonly invalid?: boolean;
  /**
   * When `value === ""` is allowed by the caller, render a disabled
   * first option using this label. The caller is responsible for
   * suppressing the empty value on submit.
   */
  readonly placeholder?: string;
  readonly containerClassName?: string;
}

// Inline caret SVG (chevron-down) painted via background-image so the
// native popup positioning is untouched. `currentColor` lets `text-*`
// utilities (including disabled token) recolor it without a parallel
// disabled selector. URL-encoded SVG; safe in single quotes inside
// double-quoted `style.backgroundImage`.
const CARET_SVG =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='currentColor' stroke-width='2'><path d='m4 6 4 4 4-4'/></svg>\")";

// Slice-6 reviewer M-1: caret SVG uses `currentColor` so it inherits
// from the element's `color` (Tailwind `text-*`). The text utility
// classes ABOVE (`text-(--color-fg-strong)` / `disabled:text-(--color-
// fg-disabled)`) control both the option text AND the caret SVG fill.
// Setting `color` via inline `style={…}` would have higher specificity
// than the utility classes and would freeze the caret color (the
// pre-fix bug).
const baseClass =
  "block w-full appearance-none rounded-md border bg-(--color-bg-base) " +
  "border-(--color-border-subtle) " +
  "text-(--color-fg-strong) " +
  "transition-colors duration-150 ease-out " +
  "hover:border-(--color-border-strong) " +
  "focus-visible:border-(--color-accent) " +
  "disabled:bg-(--color-bg-disabled) disabled:text-(--color-fg-disabled) " +
  "disabled:border-(--color-border-disabled) disabled:cursor-not-allowed " +
  "aria-invalid:border-(--color-error) " +
  "aria-invalid:focus-visible:border-(--color-error) " +
  // Caret is positioned via background; right padding leaves room for it.
  "bg-no-repeat";

const sizeClasses: Record<SelectSize, string> = {
  sm: "h-(--size-control-sm) pl-(--space-2) pr-(--space-7) text-(length:--text-xs)",
  md: "h-(--size-control-md) pl-(--space-3) pr-(--space-8) text-(length:--text-sm)",
  lg: "h-(--size-control-lg) pl-(--space-4) pr-(--space-10) text-(length:--text-base)",
};

// Caret colour is `currentColor` — inherits from `text-*` utility, so
// disabled state automatically uses fg-disabled. Position right-anchored
// per size. `color` is NOT set here — that would inline-override the
// disabled token (reviewer M-1).
const caretStyle = (size: SelectSize): React.CSSProperties => {
  const right =
    size === "sm" ? "0.5rem" : size === "md" ? "0.75rem" : "1rem";
  const dim = size === "lg" ? "1rem" : "0.875rem";
  return {
    backgroundImage: CARET_SVG,
    backgroundPosition: `right ${right} center`,
    backgroundSize: `${dim} ${dim}`,
  };
};

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    size = "md",
    invalid = false,
    options,
    placeholder,
    className,
    containerClassName,
    value,
    ...rest
  },
  ref,
): ReactNode {
  // Slice-6 FH2-H1 lock-in: preserves the global `*:focus-visible`
  // outline — does NOT set `focus-visible:outline-none`. Slice-5
  // re-audit FH2-C1 caught the same anti-pattern on raw selects.
  return (
    <select
      ref={ref}
      value={value}
      aria-invalid={invalid || undefined}
      className={cn(baseClass, sizeClasses[size], containerClassName, className)}
      style={caretStyle(size)}
      {...rest}
    >
      {placeholder !== undefined && (
        <option value="" disabled hidden={value !== ""}>
          {placeholder}
        </option>
      )}
      {options.map((opt) => (
        <option key={opt.value} value={opt.value} disabled={opt.disabled}>
          {opt.label}
        </option>
      ))}
    </select>
  );
});
