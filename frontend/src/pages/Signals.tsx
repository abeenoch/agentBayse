import { useSignals } from "../hooks/useSignals";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { SignalHeader } from "../components/SignalHeader";
import { Modal } from "../components/Modal";
import { useState } from "react";

export function Signals() {
  const queryClient = useQueryClient();
  const { data: signals, isLoading, dataUpdatedAt, refetch, isFetching } = useSignals();
  const [selected, setSelected] = useState<any | null>(null);

  const clearSignals = async () => {
    await api.post("/agent/signals/clear");
    await queryClient.invalidateQueries({ queryKey: ["signals"] });
  };

  const approve = async (id: string) => {
    await api.post("/agent/approve", null, { params: { signal_id: id } });
    await queryClient.invalidateQueries({ queryKey: ["signals"] });
  };

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-sm text-muted">Agent</p>
          <h1 className="text-2xl font-semibold">Signals</h1>
        </div>
      </header>
      <SignalHeader lastUpdated={dataUpdatedAt} onRefresh={() => refetch()} onClear={clearSignals} isLoading={isFetching || isLoading} />
      {isLoading && <p className="text-muted">Loading...</p>}
      <div className="space-y-3">
        {(signals || []).map((s: any) => (
          <button
            key={s.id}
            onClick={() => setSelected(s)}
            className="w-full text-left bg-surface border border-border rounded-xl p-4 hover:border-primary/60 transition"
          >
            <div className="flex justify-between items-center">
              <div>
                <p className="text-sm text-muted">{s.market_name}</p>
                <p className="text-lg font-semibold">{s.signal_type || s.signal}</p>
              </div>
              <div className="text-right">
                <p className="font-mono text-secondary">EV {s.expected_value?.toFixed?.(2)}</p>
                <p className="text-xs text-muted">Conf {s.confidence}%</p>
              </div>
            </div>
            <p className="text-sm text-muted mt-2">{s.reasoning}</p>
            <div className="text-xs text-muted mt-2 flex gap-2">
              <span className="px-2 py-1 rounded bg-border/40">{s.risk_level}</span>
              <span className="px-2 py-1 rounded bg-border/40">Stake {s.suggested_stake}</span>
              {s.created_at && <span className="px-2 py-1 rounded bg-border/40">At {new Date(s.created_at).toLocaleTimeString()}</span>}
            </div>
            <div className="mt-2 flex gap-2">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  approve(s.id);
                }}
                className="px-3 py-1 rounded-lg bg-primary/20 text-primary text-sm hover:bg-primary/30"
              >
                Approve
              </button>
            </div>
          </button>
        ))}
      </div>
      {!isLoading && !(signals?.length) && <p className="text-muted text-sm">No signals yet.</p>}

      <Modal open={!!selected} onClose={() => setSelected(null)} title={selected?.market_name}>
        {selected && (
          <div className="space-y-2">
            <p className="text-sm text-muted">Signal: {selected.signal_type || selected.signal}</p>
            <p className="text-sm">Confidence: {selected.confidence}%</p>
            <p className="text-sm">EV: {selected.expected_value?.toFixed?.(2)}</p>
            <p className="text-sm">Prob: {(selected.estimated_probability * 100)?.toFixed?.(1)}%</p>
            <p className="text-sm">Stake: {selected.suggested_stake}</p>
            <p className="text-sm">Risk: {selected.risk_level}</p>
            <p className="text-sm">Reasoning: {selected.reasoning}</p>
            {selected.sources?.length ? (
              <div className="text-xs text-muted">
                Sources:
                <ul className="list-disc ml-4">
                  {selected.sources.map((u: string) => (
                    <li key={u}>{u}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </Modal>
    </div>
  );
}
