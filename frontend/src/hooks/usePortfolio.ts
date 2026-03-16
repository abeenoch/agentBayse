import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function usePortfolio() {
  return useQuery({
    queryKey: ["portfolio"],
    queryFn: async () => {
      try {
        const { data } = await api.get("/portfolio");
        return data;
      } catch (err: any) {
        if (err.response?.status === 401) return null;
        throw err;
      }
    },
  });
}
