import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { CircleNotch } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";

export type ButtonVariant =
  | "primary"
  | "danger"
  // Small destructive affordance — table-row "Revoke", "Delete key", etc.
  // Distinct from `danger` (filled, solid red) and from `ghost` +
  // className override (silently broken by Tailwind v4's utility cascade
  // order — slice-4 code-review CRIT-1; slice-6 tailwind-merge adoption
  // is the proper Rule №1 fix, this variant is the explicit form).
  | "danger-ghost"
  | "secondary"
  // Slice-6 SA-BLOCK-2: SecurityTab "New API Token" was the last inline
  // button-bypass site, using a one-off outline-accent style. Promoted
  // to a named variant so the primitive owns the cascade.
  | "outline-accent"
  | "ghost"
  | "link";
export type ButtonSize = "sm" | "md" | "lg" | "xl";

const baseClass =
  "inline-flex items-center justify-center rounded-md font-medium " +
  "transition-colors duration-150 ease-out " +
  "disabled:bg-(--color-bg-disabled) disabled:text-(--color-fg-disabled) " +
  "disabled:cursor-not-allowed disabled:shadow-none";

// Slice-6 SA-RISK-1: every variant that defines a `hover:bg-*` MUST also
// pin `disabled:hover:bg-*` to suppress the hover-flash on disabled
// buttons. Without it, pointer-hover repaints the variant's hover bg for
// one frame before the base `disabled:bg-(--color-bg-disabled)` repaints
// on the next reflow — a visible flicker.
//
// Slice-6 UX-D.2: `ghost` + `danger-ghost` drop their `disabled:
// bg-transparent` overrides. Disabled affordance is universal across
// variants (per slice-5 token model); transparent disabled buttons on
// `bg-sunken` rows degraded to ~3:1 fg-on-bg, failing AA. The `link`
// variant is the genuine exception (it has `px-0`, reads as inline
// hyperlink) and keeps `disabled:bg-transparent`.
const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-(--color-accent) text-(--color-on-accent) hover:bg-(--color-accent-strong) " +
    "active:bg-(--color-accent-strong) disabled:hover:bg-(--color-bg-disabled)",
  // Slice-6 UI-CHECKPOINT-2: text now uses dedicated `--color-on-error`
  // (always white in both themes); previously borrowed `--color-on-accent`
  // which flipped to near-black in dark mode → "peachy chip" destructive
  // CTA. White-on-red is the universal stop language.
  danger:
    "bg-(--color-error) text-(--color-on-error) hover:bg-(--color-error)/90 " +
    "active:bg-(--color-error)/90 disabled:hover:bg-(--color-bg-disabled)",
  "danger-ghost":
    "bg-transparent text-(--color-error) hover:bg-(--color-error-faint) " +
    "active:bg-(--color-error-faint) disabled:hover:bg-(--color-bg-disabled)",
  secondary:
    "bg-(--color-bg-elevated) text-(--color-fg-strong) border border-(--color-border-subtle) " +
    "hover:bg-(--color-bg-sunken) disabled:hover:bg-(--color-bg-disabled)",
  "outline-accent":
    "bg-transparent text-(--color-accent) border border-(--color-accent) " +
    "hover:bg-(--color-accent-faint) active:bg-(--color-accent-faint) " +
    "disabled:hover:bg-(--color-bg-disabled)",
  ghost:
    "bg-transparent text-(--color-fg-default) hover:bg-(--color-bg-elevated) " +
    "disabled:hover:bg-(--color-bg-disabled)",
  link:
    "bg-transparent text-(--color-accent) underline-offset-4 hover:underline px-0 " +
    "disabled:bg-transparent disabled:hover:bg-transparent " +
    "disabled:hover:no-underline disabled:no-underline",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-(--size-control-sm) px-(--space-3) text-(length:--text-xs)  gap-(--space-2)",
  md: "h-(--size-control-md) px-(--space-4) text-(length:--text-sm)  gap-(--space-2)",
  lg: "h-(--size-control-lg) px-(--space-5) text-(length:--text-base) gap-(--space-2)",
  // xl reserved for the Phase-8 START/STOP hero (design-system §6.1).
  // h-20 is intentionally raw — landmark height, not a control-scale
  // size (closes UX-architect slice-5 memo Q2 carve-out).
  xl: "h-20 px-(--space-8) text-(length:--text-display-sm) font-bold gap-(--space-2) " +
    "min-w-(--width-hero-action-min) max-w-(--width-hero-action-max)",
};

// Icon-only sizes: square geometry, no padding-x, no gap-2.
const iconOnlySizes: Record<ButtonSize, string> = {
  sm: "h-(--size-control-sm) w-(--size-control-sm)",
  md: "h-(--size-control-md) w-(--size-control-md)",
  lg: "h-(--size-control-lg) w-(--size-control-lg)",
  xl: "h-20 w-20",
};

// Slice-5 UI-F3: width-stable loading. Spinner gutter is ALWAYS reserved,
// so the button doesn't reflow between idle and loading states. Spinner
// fades in / out via `visibility`, label is never replaced. Spinner size
// scales with button size (md=14, lg=16, xl=24) per UI-designer memo Q5.
const spinnerSize: Record<ButtonSize, string> = {
  sm: "h-3.5 w-3.5",
  md: "h-3.5 w-3.5",
  lg: "h-4 w-4",
  xl: "h-6 w-6",
};

// Slice-6 SA-BLOCK-1: type-level enforcement that `iconOnly={true}`
// requires `aria-label="…"`. Discriminated union splits the prop space
// so the TS compiler errors at the call site if the contract is
// violated. No runtime check — the type IS the contract. Per Rule №1.
interface BaseButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Show a leading spinner alongside the visible label. Button is
   * disabled during loading; gutter is reserved in both states so the
   * row never reflows (closes UI-F3).
   */
  loading?: boolean;
  children?: ReactNode;
}

export type ButtonProps =
  | (BaseButtonProps & { iconOnly: true; "aria-label": string })
  | (BaseButtonProps & { iconOnly?: false });

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  props,
  ref,
) {
  const {
    variant = "primary",
    size = "md",
    loading = false,
    iconOnly = false,
    className,
    children,
    disabled,
    type,
    ...rest
  } = props;

  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={cn(
        baseClass,
        variants[variant],
        iconOnly ? iconOnlySizes[size] : sizes[size],
        className,
      )}
      disabled={Boolean(disabled) || loading}
      aria-disabled={Boolean(disabled) || loading}
      aria-busy={loading}
      {...rest}
    >
      {/* Slice-5 re-audit SA-RISK-2: `link` variant is text-only (no
          fill, `px-0`), so the spinner gutter looks broken inline.
          Suppress the gutter for link; show a leading spinner only
          while actually loading. */}
      {!iconOnly && variant !== "link" && (
        <CircleNotch
          className={cn(
            spinnerSize[size],
            "shrink-0",
            // Slice-5 re-audit FH2-M1: only animate when actually
            // loading. Pre-fix, CSS `animation` continued running on
            // `visibility: hidden` elements — 50+ buttons on a settings
            // page = wasted CPU.
            loading ? "animate-spin" : "invisible",
          )}
          weight="regular"
          aria-hidden="true"
        />
      )}
      {!iconOnly && variant === "link" && loading && (
        <CircleNotch
          className={cn(spinnerSize[size], "animate-spin shrink-0")}
          weight="regular"
          aria-hidden="true"
        />
      )}
      {iconOnly && loading ? (
        <CircleNotch
          className={cn(spinnerSize[size], "animate-spin")}
          weight="regular"
          aria-hidden="true"
        />
      ) : (
        children
      )}
    </button>
  );
});
