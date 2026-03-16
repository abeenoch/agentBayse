import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function usePriceHistory(eventId?: string, marketId?: string) {
  return useQuery({
    queryKey: ["priceHistory", eventId, marketId],
    enabled: !!eventId,
    queryFn: async () => {
      const tryFetch = async (timePeriod: string, outcome?: string) => {
        const params = new URLSearchParams();
        params.append("timePeriod", timePeriod);
        if (outcome) params.append("outcome", outcome);
        if (marketId) params.append("marketId[]", marketId);
        const { data } = await api.get(`/markets/${eventId}/price-history?${params.toString()}`);
        return data || {};
      };

      try {
        // First attempt: 1W YES
        const first = await tryFetch("1W", "YES");
        const hasData = first && Object.values(first)[0]?.length;
        if (hasData) return first;

        // Fallback: 1M any outcome
        const second = await tryFetch("1M");
        return second;
      } catch {
        return {};
      }
    },
  });
}
