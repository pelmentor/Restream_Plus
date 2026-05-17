/**
 * ApiError → react-hook-form field-error mapping (Phase 9).
 *
 * Pydantic 422 envelopes carry `detail: [{loc:[…], msg, type}]`. We
 * translate field-level errors into RHF's `setError` so they render
 * underneath the input that caused them. Server is the source of
 * truth; client-side validation is UX. Unknown shapes fall through
 * (returns false) and the caller surfaces a banner/toast.
 */

import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import type { ApiError } from "./api";

interface ValidationError {
  readonly loc: readonly (string | number)[];
  readonly msg: string;
  readonly type: string;
}

function isValidationError(entry: unknown): entry is ValidationError {
  if (entry === null || typeof entry !== "object") return false;
  const e = entry as Record<string, unknown>;
  return (
    Array.isArray(e.loc) &&
    typeof e.msg === "string" &&
    typeof e.type === "string"
  );
}

/**
 * Apply server field errors to a form. Returns true iff any field
 * error was applied; callers fall back to a banner when false.
 *
 * `fieldMap` optionally remaps server field names (e.g. backend
 * `new_password` → form `new_password` is identity; backend
 * `passwords_do_not_match` → form `confirm_new_password` is a remap).
 */
export function applyApiErrorToForm<T extends FieldValues>(
  err: ApiError,
  setError: UseFormSetError<T>,
  fieldMap?: Partial<Record<string, Path<T>>>,
): boolean {
  if (err.status !== 422 || !err.validation) return false;
  let applied = false;
  err.validation.forEach((entry) => {
    if (!isValidationError(entry)) return;
    const fieldName = entry.loc[1];
    if (typeof fieldName !== "string") return;
    const target = (fieldMap?.[fieldName] ?? fieldName) as Path<T>;
    setError(target, { type: "server", message: entry.msg });
    applied = true;
  });
  return applied;
}
