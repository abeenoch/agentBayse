import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSignals(limit: number = 20, page: number = 1, eventId?: string, all: boolean = false) {
  return useQuery({
    queryKey: ["signals", limit, page, eventId, all],
    queryFn: async () => {
      try {
        const { data } = await api.get("/agent/signals", { params: { limit, page, event_id: eventId, all } });
        return data;
      } catch (err: any) {
        if (err.response?.status === 401) {
          return { signals: [], page, size: limit, count: 0 };
        }
        throw err;
      }
    },
    refetchInterval: 10_000,
  });
}
