import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useMarkets(size: number = 50, page: number = 1, category?: string) {
  return useQuery({
    queryKey: ["markets", size, page, category],
    queryFn: async () => {
      const { data } = await api.get("/markets", {
        params: { size, page, ...(category ? { category } : {}) },
      });
      return data;
    },
    refetchInterval: 30_000,
  });
}
