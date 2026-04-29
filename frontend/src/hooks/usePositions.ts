import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function usePositions() {
  return useQuery({
    queryKey: ["positions"],
    queryFn: async () => {
      const { data } = await api.get("/portfolio/positions");
      return data;
    },
    refetchInterval: 15_000,
  });
}
