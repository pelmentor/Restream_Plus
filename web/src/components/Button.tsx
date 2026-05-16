import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { CircleNotch } from "@phosphor-icons/react";

import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "danger" | "secondary" | "ghost" | "link";
export type ButtonSize = "sm" | "md" | "lg" | "xl";

const baseClass =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium " +
  "transition-colors duration-150 ease-out " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-(--color-accent) text-white hover:bg-(--color-accent-strong) " +
    "active:bg-(--color-accent-strong)",
  danger:
    "bg-(--color-error) text-white hover:bg-(--color-error)/90 " +
    "active:bg-(--color-error)/90",
  secondary:
    "bg-(--color-bg-elevated) text-(--color-fg-strong) border border-(--color-border-subtle) " +
    "hover:bg-(--color-bg-sunken)",
  ghost:
    "bg-transparent text-(--color-fg-default) hover:bg-(--color-bg-elevated)",
  link:
    "bg-transparent text-(--color-accent) underline-offset-4 hover:underline px-0",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-7  px-3 text-xs",
  md: "h-9  px-4 text-sm",
  lg: "h-11 px-5 text-base",
  // xl reserved for the Phase-8 START/STOP hero (design-system §6.1
  // width bounds enforced inline).
  xl: "h-20 px-8 text-(length:--text-display-sm) font-bold min-w-[280px] max-w-[420px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Replace the children with a spinner. The visible label is removed;
   * the original `children` is rendered visually hidden so screen
   * readers still announce it.
   */
  loading?: boolean;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading = false, className, children, disabled, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={cn(baseClass, variants[variant], sizes[size], className)}
      disabled={Boolean(disabled) || loading}
      aria-disabled={Boolean(disabled) || loading}
      aria-busy={loading}
      {...rest}
    >
      {loading ? (
        <>
          <CircleNotch className="h-4 w-4 animate-spin" weight="regular" aria-hidden="true" />
          <span className="sr-only">{children}</span>
        </>
      ) : (
        children
      )}
    </button>
  );
});
