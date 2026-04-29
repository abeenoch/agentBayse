import { useSignals } from "../hooks/useSignals";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { SignalHeader } from "../components/SignalHeader";
import { Modal } from "../components/Modal";
import { useState } from "react";

const STATUS_COLORS: Record<string, string> = {
  PENDING: "bg-yellow-500/20 text-yellow-400",
  EXECUTED: "bg-blue-500/20 text-blue-400",
  WON: "bg-secondary/20 text-secondary",
  LOST: "bg-danger/20 text-danger",
  SOLD: "bg-purple-500/20 text-purple-400",
};

const SIGNAL_COLORS: Record<string, string> = {
  BUY_YES: "text-secondary",
  BUY_NO: "text-danger",
  HOLD: "text-yellow-400",
  AVOID: "text-muted",
};

export function Signals() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(false);
  const { data, isLoading, dataUpdatedAt, refetch, isFetching } = useSignals(20, page, undefined, showAll);
  const signals = data?.signals || [];
  const [selected, setSelected] = useState<any | null>(null);

  const clearSignals = async () => {
    await api.post("/agent/signals/clear");
    qc.invalidateQueries({ queryKey: ["signals"] });
  };

  const approve = async (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    const amount = window.prompt("Stake amount (blank = suggested):");
    const params: any = { signal_id: id };
    if (amount) params.amount = Number(amount);
    await api.post("/agent/approve", null, { params });
    qc.invalidateQueries({ queryKey: ["signals"] });
  };

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-sm text-muted">Agent</p>
          <h1 className="text-2xl font-semibold">Signals</h1>
        </div>
      </header>

      <SignalHeader
        lastUpdated={dataUpdatedAt}
        onRefresh={() => refetch()}
        onClear={clearSignals}
        isLoading={isFetching || isLoading}
      />

      <div className="flex items-center gap-2 text-sm">
        <button
          onClick={() => { setShowAll(false); setPage(1); }}
          className={`px-3 py-1 rounded-lg border transition ${!showAll ? "border-primary bg-primary/20 text-primary" : "border-border text-muted hover:text-text"}`}
        >
          Active (24h)
        </button>
        <button
          onClick={() => { setShowAll(true); setPage(1); }}
          className={`px-3 py-1 rounded-lg border transition ${showAll ? "border-primary bg-primary/20 text-primary" : "border-border text-muted hover:text-text"}`}
        >
          All history
        </button>
      </div>

      {isLoading && <p className="text-muted text-sm">Loading...</p>}

      <div className="space-y-3">
        {signals.map((s: any) => {
          const sig = s.signal_type || s.signal;
          return (
            <button
              key={s.id}
              onClick={() => setSelected(s)}
              className="w-full text-left bg-surface border border-border rounded-xl p-4 hover:border-primary/60 transition"
            >
              <div className="flex justify-between items-start gap-2">
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-muted truncate">{s.market_name}</p>
                  <p className={`text-lg font-semibold ${SIGNAL_COLORS[sig] ?? "text-text"}`}>{sig}</p>
                </div>
                <div className="text-right shrink-0">
                  <p className="font-mono text-secondary text-sm">EV {s.expected_value?.toFixed?.(2)}</p>
                  <p className="text-xs text-muted">Conf {s.confidence}%</p>
                </div>
              </div>

              <p className="text-sm text-muted mt-2 line-clamp-2">{s.reasoning}</p>

              <div className="text-xs mt-2 flex flex-wrap gap-2 items-center">
                <span className={`px-2 py-0.5 rounded ${STATUS_COLORS[s.status] ?? "bg-border/40 text-muted"}`}>
                  {s.status}
                </span>
                <span className="px-2 py-0.5 rounded bg-border/40 text-muted">{s.risk_level}</span>
                <span className="px-2 py-0.5 rounded bg-border/40 text-muted">₦{s.suggested_stake}</span>
                {s.created_at && (
                  <span className="px-2 py-0.5 rounded bg-border/40 text-muted">
                    {new Date(s.created_at).toLocaleTimeString()}
                  </span>
                )}
                {s.pnl != null && (
                  <span className={`px-2 py-0.5 rounded font-mono ${s.pnl >= 0 ? "bg-secondary/20 text-secondary" : "bg-danger/20 text-danger"}`}>
                    P&L {s.pnl >= 0 ? "+" : ""}{s.pnl?.toFixed(2)}
                  </span>
                )}
              </div>

              {s.status === "PENDING" && (
                <button
                  onClick={(e) => approve(s.id, e)}
                  className="mt-3 px-3 py-1 rounded-lg bg-primary/20 text-primary text-sm hover:bg-primary/30"
                >
                  Approve & Execute
                </button>
              )}
            </button>
          );
        })}
      </div>

      {!isLoading && !signals.length && (
        <p className="text-muted text-sm">No signals yet. The agent will generate them on the next cycle.</p>
      )}

      <div className="flex items-center justify-between mt-4">
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page === 1}
          className="px-3 py-1 rounded-lg border border-border text-sm disabled:opacity-50"
        >
          Previous
        </button>
        <p className="text-xs text-muted">Page {page} · {data?.total ?? 0} total</p>
        <button
          onClick={() => setPage((p) => p + 1)}
          disabled={!signals.length || signals.length < 20 || page * 20 >= (data?.total ?? 0)}
          className="px-3 py-1 rounded-lg border border-border text-sm disabled:opacity-50"
        >
          Next
        </button>
      </div>

      <Modal open={!!selected} onClose={() => setSelected(null)} title={selected?.market_name}>
        {selected && (
          <div className="space-y-3 text-sm">
            <div className="flex gap-2 flex-wrap">
              <span className={`px-2 py-1 rounded text-xs font-semibold ${SIGNAL_COLORS[selected.signal_type || selected.signal] ?? ""}`}>
                {selected.signal_type || selected.signal}
              </span>
              <span className={`px-2 py-1 rounded text-xs ${STATUS_COLORS[selected.status] ?? "bg-border/40 text-muted"}`}>
                {selected.status}
              </span>
            </div>
            <p>Confidence: <span className="font-mono">{selected.confidence}%</span></p>
            <p>EV: <span className="font-mono text-secondary">{selected.expected_value?.toFixed?.(2)}</span></p>
            <p>Probability: <span className="font-mono">{(selected.estimated_probability * 100)?.toFixed?.(1)}%</span></p>
            <p>Stake: <span className="font-mono">₦{selected.suggested_stake}</span></p>
            <p>Risk: {selected.risk_level}</p>
            {selected.pnl != null && (
              <p>P&L: <span className={`font-mono ${selected.pnl >= 0 ? "text-secondary" : "text-danger"}`}>
                {selected.pnl >= 0 ? "+" : ""}{selected.pnl?.toFixed(2)}
              </span></p>
            )}
            <p className="text-muted">{selected.reasoning}</p>
            {selected.sources?.length > 0 && (
              <div className="text-xs text-muted">
                <p className="font-semibold mb-1">Sources</p>
                <ul className="list-disc ml-4 space-y-0.5">
                  {selected.sources.map((u: string) => (
                    <li key={u}>
                      <a href={u} target="_blank" rel="noreferrer" className="hover:text-primary underline break-all">{u}</a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {selected.status === "PENDING" && (
              <button
                onClick={(e) => { approve(selected.id, e); setSelected(null); }}
                className="px-4 py-2 rounded-lg bg-primary/20 text-primary hover:bg-primary/30"
              >
                Approve & Execute
              </button>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
