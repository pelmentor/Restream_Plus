/**
 * `useAuthReprompt()` — the Phase 9 reprompt contract.
 *
 *   const reprompt = useAuthReprompt();
 *   const grantId = await reprompt("rotate_passphrase");
 *   await destructiveMutation.mutateAsync({ grantId, ... });
 *
 * Each call hits POST /api/auth/reprompt (no grant caching across calls).
 * Resolves the grant ID, rejects on Cancel / Escape / busy / network /
 * 401 / 429. The dialog body lives in `<AuthRepromptHost>` — see that
 * file for the singleton instance pattern.
 *
 * See phase-9-design-memo §C / §E. Singleton host is mounted exactly
 * once inside `<RequireAuth>`, as a sibling of `<StatusStreamHost>`
 * and `<AppShell>`.
 */

import { createContext, useContext } from "react";

import type { RepromptScopeT } from "@/lib/schemas/auth";

export type RepromptFn = (scope: RepromptScopeT) => Promise<string>;

export const AuthRepromptContext = createContext<RepromptFn | null>(null);

export const REPROMPT_CANCELLED = "cancelled" as const;
export const REPROMPT_BUSY = "reprompt_busy" as const;

export function useAuthReprompt(): RepromptFn {
  const fn = useContext(AuthRepromptContext);
  if (fn === null) {
    throw new Error(
      "useAuthReprompt: <AuthRepromptHost> must be mounted in the React tree.",
    );
  }
  return fn;
}
