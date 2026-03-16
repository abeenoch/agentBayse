import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useTicker(marketId?: string) {
  return useQuery({
    queryKey: ["ticker", marketId],
    enabled: !!marketId,
    queryFn: async () => {
      const { data } = await api.get(`/markets/${marketId}/ticker`, { params: { outcome: "YES" } });
      return data;
    },
  });
}
