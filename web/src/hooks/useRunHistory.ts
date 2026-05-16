import { useInfiniteQuery } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { RUN_HISTORY_QUERY_KEY } from "@/lib/queryKeys";
import { RunHistoryPage, type RunHistoryPageT } from "@/lib/schemas/sessions";

const PAGE_SIZE = 50;

export function useRunHistory(): ReturnType<
  typeof useInfiniteQuery<RunHistoryPageT, ApiError>
> {
  return useInfiniteQuery<
    RunHistoryPageT,
    ApiError,
    { pages: RunHistoryPageT[]; pageParams: (string | undefined)[] },
    readonly string[],
    string | undefined
  >({
    queryKey: RUN_HISTORY_QUERY_KEY,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (pageParam !== undefined) params.set("before", pageParam);
      return apiFetch(`sessions?${params.toString()}`, {}, RunHistoryPage);
    },
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_before ?? undefined,
    refetchOnWindowFocus: false,
  });
}
