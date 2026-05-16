import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { SETTINGS_QUERY_KEY } from "@/lib/queryKeys";
import {
  IngestKeyRevealResponse,
  IngestKeyRotatedResponse,
  SettingsView,
  type IngestKeyRevealResponseT,
  type IngestKeyRotatedResponseT,
  type SettingsUpdateRequestT,
  type SettingsViewT,
} from "@/lib/schemas/settings";

export function useSettings(): {
  data: SettingsViewT | undefined;
  isPending: boolean;
  error: ApiError | null;
} {
  const q = useQuery<SettingsViewT, ApiError>({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: () => apiFetch("settings", {}, SettingsView),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  return { data: q.data, isPending: q.isPending, error: q.error ?? null };
}

export function useUpdateSettings(): ReturnType<
  typeof useMutation<SettingsViewT, ApiError, SettingsUpdateRequestT>
> {
  const queryClient = useQueryClient();
  return useMutation<SettingsViewT, ApiError, SettingsUpdateRequestT>({
    mutationFn: (body) =>
      apiFetch(
        "settings",
        {
          method: "PATCH",
          json: body,
          silenceGlobalErrors: true,
        },
        SettingsView,
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(SETTINGS_QUERY_KEY, data);
    },
    meta: { silenceGlobalErrors: true },
  });
}

export function useRotateIngestKey(): ReturnType<
  typeof useMutation<IngestKeyRotatedResponseT, ApiError, { grantId: string }>
> {
  const queryClient = useQueryClient();
  return useMutation<IngestKeyRotatedResponseT, ApiError, { grantId: string }>({
    mutationFn: ({ grantId }) =>
      apiFetch(
        "settings/rotate-ingest-key",
        {
          method: "POST",
          headers: { "X-Reprompt-Grant": grantId },
          silenceGlobalErrors: true,
        },
        IngestKeyRotatedResponse,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    },
    meta: { silenceGlobalErrors: true },
  });
}

export function useRevealIngestKey(): ReturnType<
  typeof useMutation<IngestKeyRevealResponseT, ApiError, { grantId: string }>
> {
  return useMutation<IngestKeyRevealResponseT, ApiError, { grantId: string }>({
    mutationFn: ({ grantId }) =>
      apiFetch(
        "settings/reveal-ingest-key",
        {
          method: "POST",
          headers: { "X-Reprompt-Grant": grantId },
          silenceGlobalErrors: true,
        },
        IngestKeyRevealResponse,
      ),
    meta: { silenceGlobalErrors: true },
  });
}
