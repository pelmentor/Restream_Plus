import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Slice-6 SA-RISK-3: the slice-4 CRIT-1 (Tailwind v4 cascade-order silent
// `text-(--color-error)` shadowing on `<Button variant="ghost">`) was a
// proper Rule №1 footgun. tailwind-merge resolves it at the cn() layer:
// when two utilities target the same property (e.g., a variant's
// `text-(--color-fg-default)` and a caller's `text-(--color-error)`),
// the LAST one wins — regardless of generation order. Eliminates the
// whole class of "the caller's className got silently overridden" bugs
// for Tailwind v4 arbitrary-value utilities.
//
// twMerge supports the `text-(--color-…)` arbitrary-property form via
// its arbitrary-class handling — same-key (`text-color`, `bg-color`,
// `border-color`) merges work out of the box.
export const cn = (...inputs: ClassValue[]): string => twMerge(clsx(inputs));
