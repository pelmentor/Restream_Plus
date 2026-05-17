import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Whether the user has asked for reduced motion. CSS handles 90% of
 * motion respect via `@media (prefers-reduced-motion: reduce)`; this
 * hook exists for the 10% where JS needs to know (Phase 8 sparkline
 * cadence, START spring, slide-out enter/exit). Shipped in Phase 7
 * with one consumer (ThemeToggle micro-transition) so the seam is
 * stable.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(QUERY);
    const handler = (e: MediaQueryListEvent): void => setReduced(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  return reduced;
}
