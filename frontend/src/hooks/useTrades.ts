import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useTrades(marketId?: string, limit: number = 20) {
  return useQuery({
    queryKey: ["trades", marketId, limit],
    enabled: !!marketId,
    queryFn: async () => {
      const { data } = await api.get(`/markets/${marketId}/trades`, { params: { limit } });
      return data?.trades ?? [];
    },
  });
}
