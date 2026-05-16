/**
 * Theme state machine.
 *
 * State values: `"light" | "dark" | "system"`.
 *   - `light` / `dark` set `data-theme` on `<html>` and persist to
 *     localStorage.
 *   - `system` removes the attribute AND the localStorage key — the
 *     `@media (prefers-color-scheme: dark)` block in tokens.css then
 *     decides at the CSS layer.
 *
 * The `index.html` inline bootstrapper sets `data-theme` for `light`/
 * `dark` before first paint to avoid FOUC. This module assumes that
 * has already run.
 *
 * Listeners:
 *   - `prefers-color-scheme` change → notify subscribers when in
 *     `system` mode, so JS consumers (Phase 8 sparkline) re-read tokens.
 *   - `storage` event → cross-tab sync (single-admin appliance, but
 *     two-tab edit + dashboard is a real workflow).
 */

import { createEmitter } from "@/lib/eventBus";

export type ThemeChoice = "light" | "dark" | "system";
export type EffectiveTheme = "light" | "dark";

const STORAGE_KEY = "restream-plus.theme";
const VALID_CHOICES: ReadonlySet<ThemeChoice> = new Set<ThemeChoice>([
  "light",
  "dark",
  "system",
]);

function readStoredChoice(): ThemeChoice {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw !== null && VALID_CHOICES.has(raw as ThemeChoice)) {
      return raw as ThemeChoice;
    }
  } catch {
    /* private mode / disabled storage */
  }
  return "system";
}

function applyChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") {
    root.removeAttribute("data-theme");
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* swallow */
    }
    return;
  }
  root.setAttribute("data-theme", choice);
  try {
    window.localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    /* swallow */
  }
}

function resolveEffective(choice: ThemeChoice): EffectiveTheme {
  if (choice === "light" || choice === "dark") return choice;
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export interface ThemeState {
  choice: ThemeChoice;
  effective: EffectiveTheme;
}

class ThemeManager {
  private choice: ThemeChoice = readStoredChoice();
  private emitter = createEmitter<ThemeState>();
  private mediaListener: ((e: MediaQueryListEvent) => void) | null = null;
  private storageListener: ((e: StorageEvent) => void) | null = null;
  private bound = false;

  init(): void {
    if (this.bound) return;
    this.bound = true;

    // The bootstrapper in index.html has already applied light/dark.
    // Calling applyChoice ensures `system` clears the attribute even
    // if a stale `data-theme="system"` somehow exists in the DOM.
    applyChoice(this.choice);

    if (typeof window !== "undefined" && window.matchMedia) {
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      this.mediaListener = () => {
        if (this.choice === "system") this.emit();
      };
      mql.addEventListener("change", this.mediaListener);
    }

    this.storageListener = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      const next: ThemeChoice =
        e.newValue !== null && VALID_CHOICES.has(e.newValue as ThemeChoice)
          ? (e.newValue as ThemeChoice)
          : "system";
      if (next === this.choice) return;
      this.choice = next;
      applyChoice(next);
      this.emit();
    };
    window.addEventListener("storage", this.storageListener);
  }

  set(choice: ThemeChoice): void {
    if (!VALID_CHOICES.has(choice)) return;
    if (choice === this.choice) return;
    this.choice = choice;
    applyChoice(choice);
    this.emit();
  }

  getState(): ThemeState {
    return { choice: this.choice, effective: resolveEffective(this.choice) };
  }

  subscribe(listener: (state: ThemeState) => void): () => void {
    return this.emitter.subscribe(listener);
  }

  private emit(): void {
    this.emitter.emit(this.getState());
  }
}

export const themeManager = new ThemeManager();
