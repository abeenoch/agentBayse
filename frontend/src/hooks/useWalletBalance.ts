import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useWalletBalance() {
  return useQuery({
    queryKey: ["wallet-balance"],
    queryFn: async () => {
      try {
        const { data } = await api.get("/portfolio/assets");
        const assets: any[] = data?.assets || [];
        const ngn = assets.find((a: any) => a.symbol?.toUpperCase() === "NGN");
        return ngn?.availableBalance ?? null;
      } catch {
        return null;
      }
    },
    refetchInterval: 15_000,
  });
}
