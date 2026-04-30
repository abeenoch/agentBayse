import { useMarkets } from "../hooks/useMarkets";
import { useSignals } from "../hooks/useSignals";
import { usePortfolio } from "../hooks/usePortfolio";
import { usePositions } from "../hooks/usePositions";
import { useWalletBalance } from "../hooks/useWalletBalance";
import { Modal } from "../components/Modal";
import { useState } from "react";
import { useActivities } from "../hooks/useActivities";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

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

export function Dashboard() {
  const qc = useQueryClient();
  const { data: marketsResp, dataUpdatedAt: marketsUpdated } = useMarkets(50, 1);
  const { data: signalsResp, dataUpdatedAt: signalsUpdated } = useSignals(10, 1, undefined, false);
  const { data: activities } = useActivities(1, 30);
  const { data: portfolio } = usePortfolio();
  const { data: positionsData, dataUpdatedAt: positionsUpdated } = usePositions();
  const { data: walletBalance } = useWalletBalance();
  const markets = marketsResp?.events || [];
  const signals = signalsResp?.signals || [];
  const positions = positionsData?.positions || [];
  const [selectedSignal, setSelectedSignal] = useState<any | null>(null);
  const [selectedMarket, setSelectedMarket] = useState<any | null>(null);
  const [selectedActivity, setSelectedActivity] = useState<any | null>(null);

  const approve = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const amount = window.prompt("Stake amount (blank = suggested):");
    const params: any = { signal_id: id };
    if (amount) params.amount = Number(amount);
    await api.post("/agent/approve", null, { params });
    qc.invalidateQueries({ queryKey: ["signals"] });
  };

  const pnlPct = portfolio?.portfolioPercentageChange ?? 0;
  const pnlColor = pnlPct >= 0 ? "text-secondary" : "text-danger";

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-sm text-muted">Overview</p>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="w-2 h-2 rounded-full bg-secondary animate-pulse" />
          Live
        </div>
      </header>

      {/* Stats row */}
      <div className="grid gap-4 md:grid-cols-3">
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Wallet Balance</p>
          {walletBalance != null ? (
            <>
              <p className="text-2xl font-mono font-semibold">
                ₦{walletBalance.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </p>
              <p className="text-xs text-muted">
                In positions: ₦{(portfolio?.portfolioCost ?? 0).toLocaleString()}
              </p>
            </>
          ) : (
            <p className="text-muted text-sm">–</p>
          )}
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">P&L</p>
          {portfolio ? (
            <p className={`text-2xl font-mono font-semibold ${pnlColor}`}>
              {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
            </p>
          ) : (
            <p className="text-muted text-sm">–</p>
          )}
          <p className="text-xs text-muted">Mark to market</p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Open Positions</p>
          {portfolio ? (
            <p className="text-2xl font-mono font-semibold">
              {portfolio?.outcomeBalances?.length ?? 0}
            </p>
          ) : (
            <p className="text-muted text-sm">–</p>
          )}
          <p className="text-xs text-muted">Active bets</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {/* Signals */}
        <section className="bg-surface border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Active Bets</h2>
            <p className="text-xs text-muted">
              {positionsUpdated ? new Date(positionsUpdated).toLocaleTimeString() : "–"}
            </p>
          </div>
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {positions.map((p: any, i: number) => {
              const cost = p.cost ?? null;
              const currentVal = p.current_value ?? null;
              const pnlAbs = (cost != null && currentVal != null) ? currentVal - cost : p.pnl ?? null;
              const pnlPct = (cost != null && cost > 0 && pnlAbs != null)
                ? (pnlAbs / cost) * 100
                : p.pnl_pct ?? null;
              const isUp = pnlAbs != null && pnlAbs >= 0;
              const pnlColor = pnlAbs == null ? "text-muted" : isUp ? "text-secondary" : "text-danger";
              const outcomeColor = (p.outcome || "").toUpperCase().includes("YES") ? "text-secondary" : "text-danger";

              return (
                <div
                  key={p.market_id || i}
                  className="w-full text-left border border-border rounded-lg p-3 bg-[#0F1016]"
                >
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-muted truncate">{p.market_name}</p>
                      <span className={`text-xs font-bold px-1.5 py-0.5 rounded bg-border/40 ${outcomeColor}`}>
                        {(p.outcome || "").toUpperCase()}
                      </span>
                    </div>
                    <div className="text-right shrink-0">
                      {pnlAbs != null && (
                        <p className={`font-mono text-sm font-semibold ${pnlColor}`}>
                          {isUp ? "+" : ""}₦{pnlAbs.toFixed(2)}
                        </p>
                      )}
                      {pnlPct != null && (
                        <p className={`text-xs font-mono ${pnlColor}`}>
                          {isUp ? "+" : ""}{pnlPct.toFixed(1)}%
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex justify-between mt-2 text-xs text-muted font-mono">
                    <span>Staked: <span className="text-text">₦{cost?.toFixed(2) ?? "–"}</span></span>
                    <span>Now: <span className="text-text">₦{currentVal?.toFixed(2) ?? "–"}</span></span>
                  </div>
                </div>
              );
            })}
            {!positions.length && <p className="text-muted text-sm">No active bets.</p>}
          </div>
        </section>

        {/* Markets */}
        <section className="bg-surface border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Active Markets</h2>
            <p className="text-xs text-muted">
              {marketsUpdated ? new Date(marketsUpdated).toLocaleTimeString() : "–"}
            </p>
          </div>
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {markets.slice(0, 10).map((evt: any) => (
              <button
                key={evt.id}
                onClick={() => setSelectedMarket(evt)}
                className="w-full flex justify-between border border-border rounded-lg p-3 bg-[#0F1016] hover:border-primary/60 transition text-left"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-muted uppercase">{evt.category}</p>
                  <p className="font-semibold text-sm truncate">{evt.title}</p>
                </div>
                <div className="text-right ml-2 shrink-0">
                  <p className="text-secondary font-mono text-xs">{evt.markets?.[0]?.outcome1Price?.toFixed(2) ?? "--"}</p>
                  <p className="text-danger font-mono text-xs">{evt.markets?.[0]?.outcome2Price?.toFixed(2) ?? "--"}</p>
                </div>
              </button>
            ))}
            {!markets.length && <p className="text-muted text-sm">No markets loaded.</p>}
          </div>
        </section>

        {/* Activity */}
        <section className="bg-surface border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Activity</h2>
          </div>
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {(activities || []).map((a: any) => (
              <button
                key={a.id}
                onClick={() => setSelectedActivity(a)}
                className="w-full text-left border border-border rounded-lg p-3 bg-[#0F1016] hover:border-primary/60 transition"
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-muted truncate">{a.marketTitle || a.eventTitle}</p>
                    <p className="text-sm font-semibold">{a.type?.replace(/_/g, " ")}</p>
                  </div>
                  <div className="text-right ml-2 shrink-0">
                    <span className="text-secondary font-mono text-xs block">
                      {a.amount ?? a.totalCost ?? "--"}
                    </span>
                    {a.outcome && (
                      <span className={`text-xs font-bold ${
                        String(a.outcome).toUpperCase() === "YES" ? "text-secondary" : "text-danger"
                      }`}>
                        {String(a.outcome).toUpperCase()}
                      </span>
                    )}
                  </div>
                </div>
                <p className="text-xs text-muted mt-1">
                  {a.createdAt ? new Date(a.createdAt).toLocaleTimeString() : ""}
                </p>
              </button>
            ))}
            {!(activities?.length) && <p className="text-muted text-sm">No activity yet.</p>}
          </div>
        </section>
      </div>

      {/* Modals */}
      <Modal open={!!selectedSignal} onClose={() => setSelectedSignal(null)} title={selectedSignal?.market_name}>
        {selectedSignal && (
          <div className="space-y-2 text-sm">
            <div className="flex gap-2 flex-wrap">
              <span className={`px-2 py-1 rounded text-xs font-semibold ${SIGNAL_COLORS[selectedSignal.signal_type || selectedSignal.signal] ?? ""}`}>
                {selectedSignal.signal_type || selectedSignal.signal}
              </span>
              <span className={`px-2 py-1 rounded text-xs ${STATUS_COLORS[selectedSignal.status] ?? "bg-border/40 text-muted"}`}>
                {selectedSignal.status}
              </span>
            </div>
            <p>Confidence: <span className="font-mono">{selectedSignal.confidence}%</span></p>
            <p>EV: <span className="font-mono text-secondary">{selectedSignal.expected_value?.toFixed?.(2)}</span></p>
            <p>Probability: <span className="font-mono">{(selectedSignal.estimated_probability * 100)?.toFixed?.(1)}%</span></p>
            <p>Stake: <span className="font-mono">₦{selectedSignal.suggested_stake}</span></p>
            <p>Risk: {selectedSignal.risk_level}</p>
            <p className="text-muted">{selectedSignal.reasoning}</p>
            {selectedSignal.sources?.length > 0 && (
              <div className="text-xs text-muted">
                <p className="font-semibold mb-1">Sources</p>
                <ul className="list-disc ml-4 space-y-0.5">
                  {selectedSignal.sources.map((u: string) => (
                    <li key={u}><a href={u} target="_blank" rel="noreferrer" className="hover:text-primary underline">{u}</a></li>
                  ))}
                </ul>
              </div>
            )}
            {selectedSignal.status === "PENDING" && (
              <button
                onClick={(e) => { approve(selectedSignal.id, e); setSelectedSignal(null); }}
                className="px-4 py-2 rounded-lg bg-primary/20 text-primary hover:bg-primary/30 text-sm"
              >
                Approve & Execute
              </button>
            )}
          </div>
        )}
      </Modal>

      <Modal open={!!selectedMarket} onClose={() => setSelectedMarket(null)} title={selectedMarket?.title}>
        {selectedMarket && (
          <div className="space-y-2 text-sm">
            <p className="text-muted uppercase text-xs">{selectedMarket.category}</p>
            <p>Liquidity: <span className="font-mono">{selectedMarket.liquidity ?? "--"}</span></p>
            <p>Volume: <span className="font-mono">{selectedMarket.totalVolume ?? "--"}</span></p>
            {selectedMarket.markets?.[0] && (
              <div className="flex gap-4 font-mono">
                <span className="text-secondary">YES {selectedMarket.markets[0].outcome1Price}</span>
                <span className="text-danger">NO {selectedMarket.markets[0].outcome2Price}</span>
              </div>
            )}
            {selectedMarket.description && <p className="text-muted">{selectedMarket.description}</p>}
          </div>
        )}
      </Modal>

      <Modal open={!!selectedActivity} onClose={() => setSelectedActivity(null)} title={selectedActivity?.marketTitle || selectedActivity?.eventTitle}>
        {selectedActivity && (
          <div className="space-y-2 text-sm">
            <p>Type: {selectedActivity.type?.replace(/_/g, " ")}</p>
            <p>Amount: <span className="font-mono">{selectedActivity.amount}</span></p>
            <p>Size: <span className="font-mono">{selectedActivity.size}</span></p>
            <p>Price: <span className="font-mono">{selectedActivity.price}</span></p>
            <p>Status: {selectedActivity.status}</p>
            <p>Outcome: {selectedActivity.outcome}</p>
            {selectedActivity.payout && <p>Payout: <span className="font-mono text-secondary">{selectedActivity.payout}</span></p>}
            <p className="text-muted text-xs">{selectedActivity.createdAt ? new Date(selectedActivity.createdAt).toLocaleString() : ""}</p>
          </div>
        )}
      </Modal>
    </div>
  );
}
