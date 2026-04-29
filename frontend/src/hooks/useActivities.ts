import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useActivities(page: number = 1, size: number = 20, type?: string) {
  return useQuery({
    queryKey: ["activities", page, size, type],
    queryFn: async () => {
      const { data } = await api.get("/portfolio/activities", {
        params: { page, size, type },
      });
      return data?.activities ?? [];
    },
    refetchInterval: 20_000,
  });
}
