import {
  MutationCache,
  QueryCache,
  QueryClient,
  type DefaultOptions,
} from "@tanstack/react-query";

import { ApiError, globalApiErrorBus } from "./api";

const NON_RETRYABLE_STATUSES = new Set([401, 403, 422, 429]);

const defaults: DefaultOptions = {
  queries: {
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
    retry: (failureCount, error): boolean => {
      if (!(error instanceof ApiError)) return failureCount < 1;
      if (NON_RETRYABLE_STATUSES.has(error.status)) return false;
      if (error.status === 0) return failureCount < 1;
      return error.status >= 500 && failureCount < 1;
    },
    retryDelay: (attemptIndex): number =>
      Math.min(1_000 * Math.pow(2, attemptIndex), 8_000),
  },
  mutations: {
    retry: false,
  },
};

/**
 * The cache `onError` callbacks rebroadcast to `globalApiErrorBus` so
 * the top-level App subscriber sees errors from queries the user
 * never directly initiated (background refetches, dependent queries).
 *
 * Per review M-1: `apiFetch`'s own `silenceGlobalErrors` flag only
 * suppresses its direct emit; without a guard here the MutationCache
 * would re-broadcast and race the inline handler. Mutations / queries
 * that handle their own errors set `meta: { silenceGlobalErrors: true }`
 * and we honor it here.
 */
function isSilenced(meta: Readonly<Record<string, unknown>> | undefined): boolean {
  return meta?.silenceGlobalErrors === true;
}

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: defaults,
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (!(error instanceof ApiError)) return;
        if (isSilenced(query.meta)) return;
        globalApiErrorBus.emit(error);
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (!(error instanceof ApiError)) return;
        if (isSilenced(mutation.meta)) return;
        globalApiErrorBus.emit(error);
      },
    }),
  });
}
