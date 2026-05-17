/**
 * Central TanStack QueryKey constants.
 *
 * Cross-hook invalidation (per phase-9-design-memo §O) needs stable
 * importable keys; Phase 7/8's colocated `RUN_STATE_QUERY_KEY` inside
 * `useRunState.ts` stays where it is and is re-exported here to keep
 * one canonical import path without churning Phase 8 files (Rule №2).
 */

export const SETTINGS_QUERY_KEY = ["settings"] as const;
export const API_TOKENS_QUERY_KEY = ["settings", "tokens"] as const;
export const HTTP_SESSIONS_QUERY_KEY = ["security", "sessions"] as const;
export const RUN_HISTORY_QUERY_KEY = ["sessions"] as const;
export const ABOUT_QUERY_KEY = ["about"] as const;

export { RUN_STATE_QUERY_KEY } from "@/hooks/useRunState";
export { TARGETS_QUERY_KEY } from "@/hooks/useTargets";
