import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export type AgentConfig = {
  auto_trade: boolean;
  categories: string[];
  max_trades_per_hour: number;
  max_trades_per_day: number;
  balance_floor: number;
};

export function useAgentConfig() {
  return useQuery({
    queryKey: ["agent-config"],
    queryFn: async () => {
      const { data } = await api.get("/agent/config");
      return data as AgentConfig;
    },
  });
}

export function useSaveAgentConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Partial<AgentConfig>) => {
      const { data } = await api.post("/agent/config", payload);
      return data as AgentConfig;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-config"] }),
  });
}
