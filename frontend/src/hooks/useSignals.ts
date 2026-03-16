import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useSignals(limit: number = 20) {
  return useQuery({
    queryKey: ["signals", limit],
    queryFn: async () => {
      try {
        const { data } = await api.get("/agent/signals", { params: { limit } });
        return data;
      } catch (err: any) {
        if (err.response?.status === 401) {
          return [];
        }
        throw err;
      }
    },
  });
}
