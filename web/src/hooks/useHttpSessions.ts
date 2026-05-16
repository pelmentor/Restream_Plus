import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { HTTP_SESSIONS_QUERY_KEY } from "@/lib/queryKeys";
import { HttpSessionList, type HttpSessionViewT } from "@/lib/schemas/sessions";

export function useHttpSessions(): {
  data: readonly HttpSessionViewT[] | undefined;
  isPending: boolean;
  error: ApiError | null;
} {
  const q = useQuery<readonly HttpSessionViewT[], ApiError>({
    queryKey: HTTP_SESSIONS_QUERY_KEY,
    queryFn: () => apiFetch("security/sessions", {}, HttpSessionList),
    refetchOnWindowFocus: false,
  });
  return { data: q.data, isPending: q.isPending, error: q.error ?? null };
}

export function useRevokeHttpSession(): ReturnType<
  typeof useMutation<void, ApiError, { fingerprint: string }>
> {
  const qc = useQueryClient();
  return useMutation<void, ApiError, { fingerprint: string }>({
    mutationFn: async ({ fingerprint }) => {
      await apiFetch(`security/sessions/${fingerprint}`, {
        method: "DELETE",
        silenceGlobalErrors: true,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: HTTP_SESSIONS_QUERY_KEY });
    },
    meta: { silenceGlobalErrors: true },
  });
}
