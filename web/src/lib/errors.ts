/**
 * Mirror of `app/api/errors.py::ErrorCode`. Frozen string union; if the
 * backend grows a new code, both sides update together.
 *
 * Phase 7 deliberately mirrors the existing 21 codes verbatim rather
 * than generating from OpenAPI (Rule №2: no cross-language codegen).
 */
export const ERROR_CODES = [
  "service_locked",
  "service_unlocking",
  "unlock_failed",
  "unlock_rate_limited",
  "unauthorized",
  "invalid_credentials",
  "rate_limited",
  "reprompt_required",
  "reprompt_invalid_or_expired",
  "target_not_found",
  "target_in_use",
  "target_misconfigured",
  "credential_required",
  "unknown_target_type",
  "invalid_target_url",
  "illegal_run_state",
  "ingest_key_invalid",
  "api_token_not_found",
  "auth_state_not_initialised",
  "not_found",
  "bad_request",
  // Phase 9 additions — see phase-9-design-memo.md §K.
  "credential_not_found",
  "new_passphrase_too_weak",
  "same_passphrase",
  "run_active",
  "session_not_found",
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

const ERROR_CODE_SET: ReadonlySet<string> = new Set<string>(ERROR_CODES);

export function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && ERROR_CODE_SET.has(value);
}
