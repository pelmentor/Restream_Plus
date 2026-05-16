import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { RUN_STATE_QUERY_KEY, TARGETS_QUERY_KEY } from "@/lib/queryKeys";
import { RevealCredentialResponse, type RevealCredentialResponseT } from "@/lib/schemas/settings";
import {
  CredentialSummary,
  Target,
  type CredentialSummaryT,
  type TargetCreateRequestT,
  type TargetT,
  type TargetUpdateRequestT,
} from "@/lib/schemas/targets";

function invalidateTargetsAndRun(qc: ReturnType<typeof useQueryClient>): void {
  void qc.invalidateQueries({ queryKey: TARGETS_QUERY_KEY });
  void qc.invalidateQueries({ queryKey: RUN_STATE_QUERY_KEY });
}

export function useCreateTarget(): ReturnType<
  typeof useMutation<TargetT, ApiError, TargetCreateRequestT>
> {
  const qc = useQueryClient();
  return useMutation<TargetT, ApiError, TargetCreateRequestT>({
    mutationFn: (body) =>
      apiFetch(
        "targets",
        { method: "POST", json: body, silenceGlobalErrors: true },
        Target,
      ),
    onSuccess: () => invalidateTargetsAndRun(qc),
    meta: { silenceGlobalErrors: true },
  });
}

export function useUpdateTarget(): ReturnType<
  typeof useMutation<TargetT, ApiError, { id: string; body: TargetUpdateRequestT }>
> {
  const qc = useQueryClient();
  return useMutation<TargetT, ApiError, { id: string; body: TargetUpdateRequestT }>({
    mutationFn: ({ id, body }) =>
      apiFetch(
        `targets/${id}`,
        { method: "PATCH", json: body, silenceGlobalErrors: true },
        Target,
      ),
    onSuccess: () => invalidateTargetsAndRun(qc),
    meta: { silenceGlobalErrors: true },
  });
}

export function useDeleteTarget(): ReturnType<
  typeof useMutation<void, ApiError, { id: string; grantId: string }>
> {
  const qc = useQueryClient();
  return useMutation<void, ApiError, { id: string; grantId: string }>({
    mutationFn: async ({ id, grantId }) => {
      await apiFetch(`targets/${id}`, {
        method: "DELETE",
        headers: { "X-Reprompt-Grant": grantId },
        silenceGlobalErrors: true,
      });
    },
    onSuccess: () => invalidateTargetsAndRun(qc),
    meta: { silenceGlobalErrors: true },
  });
}

export function useSetCredential(): ReturnType<
  typeof useMutation<CredentialSummaryT, ApiError, { id: string; streamKey: string }>
> {
  const qc = useQueryClient();
  return useMutation<CredentialSummaryT, ApiError, { id: string; streamKey: string }>({
    mutationFn: ({ id, streamKey }) =>
      apiFetch(
        `targets/${id}/credential`,
        {
          method: "PUT",
          json: { stream_key: streamKey },
          silenceGlobalErrors: true,
        },
        CredentialSummary,
      ),
    onSuccess: () => invalidateTargetsAndRun(qc),
    meta: { silenceGlobalErrors: true },
  });
}

export function useClearCredential(): ReturnType<
  typeof useMutation<void, ApiError, { id: string; grantId: string }>
> {
  const qc = useQueryClient();
  return useMutation<void, ApiError, { id: string; grantId: string }>({
    mutationFn: async ({ id, grantId }) => {
      await apiFetch(`targets/${id}/credential`, {
        method: "DELETE",
        headers: { "X-Reprompt-Grant": grantId },
        silenceGlobalErrors: true,
      });
    },
    onSuccess: () => invalidateTargetsAndRun(qc),
    meta: { silenceGlobalErrors: true },
  });
}

export function useRevealCredential(): ReturnType<
  typeof useMutation<RevealCredentialResponseT, ApiError, { id: string; grantId: string }>
> {
  return useMutation<RevealCredentialResponseT, ApiError, { id: string; grantId: string }>({
    mutationFn: ({ id, grantId }) =>
      apiFetch(
        `targets/${id}/reveal-credential`,
        {
          method: "POST",
          headers: { "X-Reprompt-Grant": grantId },
          silenceGlobalErrors: true,
        },
        RevealCredentialResponse,
      ),
    meta: { silenceGlobalErrors: true },
  });
}
