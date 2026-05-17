/**
 * Thin typed fetch wrapper around the FastAPI surface.
 *
 * Pure: no React, no router, no top-level side effects beyond the
 * `globalApiErrorBus.emit(err)` on a thrown ApiError so the App-level
 * subscriber can route 401 → /login and 503-locked → /unlock.
 *
 * Callers that need to handle their own auth/locked errors (Login,
 * Unlock pages themselves) catch and branch on `err.code`.
 *
 * Same-origin only. The `__Host-rp_session` cookie is HttpOnly +
 * Secure + Path=/ — `credentials: "same-origin"` is correct and is
 * NOT `"include"` (which would trigger cross-origin CORS preflight).
 */

import type { z } from "zod";

import { createEmitter } from "./eventBus";
import { isErrorCode, type ErrorCode } from "./errors";

export type ApiErrorCode = ErrorCode | "unknown" | "network_error";

export interface ApiErrorInit {
  code: ApiErrorCode;
  status: number;
  retryAfter?: number;
  validation?: readonly unknown[];
  message?: string;
}

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly retryAfter?: number;
  readonly validation?: readonly unknown[];

  constructor(args: ApiErrorInit) {
    super(args.message ?? args.code);
    this.name = "ApiError";
    this.code = args.code;
    this.status = args.status;
    if (args.retryAfter !== undefined) this.retryAfter = args.retryAfter;
    if (args.validation !== undefined) this.validation = args.validation;
  }
}

/**
 * Per phase-7-design-memo §E: the bus is the seam between pure fetch
 * code and the React/Router-aware handler in App.tsx. Subscribers are
 * added once at app start; unsubscribe is unused in v1 (the App
 * lifetime is the page lifetime).
 */
export const globalApiErrorBus = createEmitter<ApiError>();

export interface ApiFetchInit extends Omit<RequestInit, "body"> {
  json?: unknown;
  /**
   * When true, an ApiError raised by this call is NOT emitted to
   * `globalApiErrorBus`. Use for Login/Unlock pages that handle 401 /
   * 503 inline — they would otherwise trigger the global redirect.
   */
  silenceGlobalErrors?: boolean;
}

function buildUrl(path: string): string {
  if (path.startsWith("/")) return path;
  return `/api/${path}`;
}

function parseRetryAfter(headers: Headers): number | undefined {
  const raw = headers.get("Retry-After");
  if (raw === null) return undefined;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

export async function apiFetch<T>(
  path: string,
  init: ApiFetchInit = {},
  schema?: z.ZodType<T>,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");

  const requestInit: RequestInit = {
    ...init,
    headers,
    credentials: "same-origin",
  };
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    requestInit.body = JSON.stringify(init.json);
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path), requestInit);
  } catch (cause) {
    const err = new ApiError({
      code: "network_error",
      status: 0,
      message: cause instanceof Error ? cause.message : "network_error",
    });
    if (!init.silenceGlobalErrors) globalApiErrorBus.emit(err);
    throw err;
  }

  if (res.status === 204) return undefined as T;

  const ct = res.headers.get("Content-Type") ?? "";
  let payload: unknown = null;
  if (ct.includes("application/json")) {
    try {
      payload = (await res.json()) as unknown;
    } catch {
      // Malformed JSON body. Fall through to error handling on !res.ok;
      // on 2xx with malformed body we throw below.
    }
  } else if (res.status >= 400) {
    payload = { detail: await res.text() };
  }

  if (!res.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    const retryAfter = parseRetryAfter(res.headers);
    if (Array.isArray(detail)) {
      const err = new ApiError({
        code: "bad_request",
        status: res.status,
        validation: detail,
      });
      if (!init.silenceGlobalErrors) globalApiErrorBus.emit(err);
      throw err;
    }
    const code: ApiErrorCode = isErrorCode(detail)
      ? detail
      : res.status === 401
        ? "unauthorized"
        : "unknown";
    const err = new ApiError({
      code,
      status: res.status,
      ...(retryAfter !== undefined ? { retryAfter } : {}),
    });
    if (!init.silenceGlobalErrors) globalApiErrorBus.emit(err);
    throw err;
  }

  if (schema === undefined) return payload as T;
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError({
      code: "unknown",
      status: res.status,
      message: `response_schema_violation: ${parsed.error.message}`,
    });
  }
  return parsed.data;
}
