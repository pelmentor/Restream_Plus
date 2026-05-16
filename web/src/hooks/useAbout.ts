import { useQuery } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { ABOUT_QUERY_KEY } from "@/lib/queryKeys";
import { AboutView, type AboutViewT } from "@/lib/schemas/about";

export function useAbout(): {
  data: AboutViewT | undefined;
  isPending: boolean;
  error: ApiError | null;
} {
  const q = useQuery<AboutViewT, ApiError>({
    queryKey: ABOUT_QUERY_KEY,
    queryFn: () => apiFetch("about", {}, AboutView),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  return { data: q.data, isPending: q.isPending, error: q.error ?? null };
}
