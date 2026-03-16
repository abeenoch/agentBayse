import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useMarkets(size: number = 50, page: number = 1) {
  return useQuery({
    queryKey: ["markets", size, page],
    queryFn: async () => {
      const { data } = await api.get("/markets", { params: { size, page } });
      return data.events || [];
    },
  });
}
