import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { API_TOKENS_QUERY_KEY } from "@/lib/queryKeys";
import {
  ApiTokenCreatedResponse,
  ApiTokenList,
  type ApiTokenCreatedResponseT,
  type ApiTokenViewT,
} from "@/lib/schemas/apiTokens";

export function useApiTokens(): {
  data: readonly ApiTokenViewT[] | undefined;
  isPending: boolean;
  error: ApiError | null;
} {
  const q = useQuery<readonly ApiTokenViewT[], ApiError>({
    queryKey: API_TOKENS_QUERY_KEY,
    queryFn: () => apiFetch("security/tokens", {}, ApiTokenList),
    refetchOnWindowFocus: false,
  });
  return { data: q.data, isPending: q.isPending, error: q.error ?? null };
}

export function useCreateApiToken(): ReturnType<
  typeof useMutation<ApiTokenCreatedResponseT, ApiError, { label: string }>
> {
  const qc = useQueryClient();
  return useMutation<ApiTokenCreatedResponseT, ApiError, { label: string }>({
    mutationFn: (body) =>
      apiFetch(
        "security/tokens",
        { method: "POST", json: body, silenceGlobalErrors: true },
        ApiTokenCreatedResponse,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY });
    },
    meta: { silenceGlobalErrors: true },
  });
}

export function useRevokeApiToken(): ReturnType<
  typeof useMutation<void, ApiError, { id: string; grantId: string }>
> {
  const qc = useQueryClient();
  return useMutation<void, ApiError, { id: string; grantId: string }>({
    mutationFn: async ({ id, grantId }) => {
      await apiFetch(`security/tokens/${id}`, {
        method: "DELETE",
        headers: { "X-Reprompt-Grant": grantId },
        silenceGlobalErrors: true,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY });
    },
    meta: { silenceGlobalErrors: true },
  });
}
