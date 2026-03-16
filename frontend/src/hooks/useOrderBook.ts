import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useOrderBook(outcomeIds: string[] | undefined) {
  return useQuery({
    queryKey: ["orderbook", outcomeIds?.join(",")],
    enabled: !!outcomeIds && outcomeIds.length > 0,
    queryFn: async () => {
      const params = new URLSearchParams();
      outcomeIds?.forEach((id) => params.append("outcomeId[]", id));
      params.append("depth", "5");
      const { data } = await api.get(`/markets/orderbook?${params.toString()}`);
      return data;
    },
  });
}
