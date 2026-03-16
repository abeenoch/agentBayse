import { useMarkets } from "../hooks/useMarkets";
import { useSignals } from "../hooks/useSignals";
import { usePortfolio } from "../hooks/usePortfolio";
import { Modal } from "../components/Modal";
import { useState } from "react";
import { useActivities } from "../hooks/useActivities";

export function Dashboard() {
  const { data: markets } = useMarkets(50, 1);
  const { data: signals } = useSignals(10);
  const { data: activities } = useActivities(1, 30);
  const { data: portfolio } = usePortfolio();
  const [selectedSignal, setSelectedSignal] = useState<any | null>(null);
  const [selectedMarket, setSelectedMarket] = useState<any | null>(null);
  const [selectedActivity, setSelectedActivity] = useState<any | null>(null);

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-sm text-muted">Overview</p>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
        </div>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Portfolio Value</p>
          {portfolio ? (
            <>
              <p className="text-2xl font-mono font-semibold">
                ₦{(portfolio?.portfolioCurrentValue ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </p>
              <p className="text-xs text-muted">Invested ₦{(portfolio?.portfolioCost ?? 0).toLocaleString()}</p>
            </>
          ) : (
            <p className="text-muted text-sm">Login to view</p>
          )}
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">P&L %</p>
          {portfolio ? (
            <p className={`text-2xl font-mono font-semibold ${((portfolio?.portfolioPercentageChange ?? 0) >= 0 ? "text-secondary" : "text-danger")}`}>
              {(portfolio?.portfolioPercentageChange ?? 0).toFixed(2)}%
            </p>
          ) : (
            <p className="text-muted text-sm">Login to view</p>
          )}
          <p className="text-xs text-muted">Mark to market</p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Positions</p>
          {portfolio ? (
            <p className="text-2xl font-mono font-semibold">
              {portfolio?.outcomeBalances?.length ?? 0}
            </p>
          ) : (
            <p className="text-muted text-sm">Login to view</p>
          )}
          <p className="text-xs text-muted">Open positions</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <section className="bg-surface border border-border rounded-xl p-4 col-span-1 md:col-span-1">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Latest Signals</h2>
          </div>
          <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
            {(signals || []).map((s: any) => (
              <button
                key={s.id}
                onClick={() => setSelectedSignal(s)}
                className="w-full text-left border border-border rounded-lg p-3 bg-[#0F1016] hover:border-primary/60 transition"
              >
                <div className="flex justify-between items-center">
                  <div>
                    <p className="text-sm text-muted">{s.market_name}</p>
                    <p className="text-lg font-semibold">{s.signal_type || s.signal}</p>
                  </div>
                  <span className="text-secondary font-mono">{s.expected_value?.toFixed?.(2)} EV</span>
                </div>
                <p className="text-sm text-muted mt-2 line-clamp-2">{s.reasoning}</p>
              </button>
            ))}
            {!signals?.length && <p className="text-muted text-sm">No signals yet.</p>}
          </div>
        </section>

        <section className="bg-surface border border-border rounded-xl p-4 col-span-1 md:col-span-1">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Active Markets</h2>
          </div>
          <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
            {(markets || []).slice(0, 10).map((evt: any) => (
              <button
                key={evt.id}
                onClick={() => setSelectedMarket(evt)}
                className="w-full flex justify-between border border-border rounded-lg p-3 bg-[#0F1016] hover:border-primary/60 transition text-left"
              >
                <div>
                  <p className="text-sm text-muted">{evt.category}</p>
                  <p className="font-semibold">{evt.title}</p>
                </div>
                <div className="text-right">
                  <p className="text-secondary font-mono">{evt.liquidity ?? "--"} liq</p>
                  <p className="text-muted text-xs">vol {evt.totalVolume ?? "--"}</p>
                </div>
              </button>
            ))}
            {!markets?.length && <p className="text-muted text-sm">No markets loaded.</p>}
          </div>
        </section>

        <section className="bg-surface border border-border rounded-xl p-4 col-span-1 md:col-span-1">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Prediction History</h2>
          </div>
          <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
            {(activities || []).map((a: any) => (
              <button
                key={a.id}
                onClick={() => setSelectedActivity(a)}
                className="w-full text-left border border-border rounded-lg p-3 bg-[#0F1016] hover:border-primary/60 transition"
              >
                <div className="flex justify-between">
                  <div>
                    <p className="text-sm text-muted">{a.marketTitle || a.eventTitle}</p>
                    <p className="font-semibold">{a.type}</p>
                  </div>
                  <span className="text-secondary font-mono">{a.amount ?? a.totalCost ?? "--"}</span>
                </div>
                <p className="text-xs text-muted mt-1">
                  {a.createdAt ? new Date(a.createdAt).toLocaleString() : ""}
                </p>
              </button>
            ))}
            {!activities?.length && <p className="text-muted text-sm">No history yet.</p>}
          </div>
        </section>
      </div>

      <Modal
        open={!!selectedSignal}
        onClose={() => setSelectedSignal(null)}
        title={selectedSignal?.market_name}
      >
        {selectedSignal && (
          <div className="space-y-2">
            <p className="text-sm text-muted">Signal: {selectedSignal.signal_type || selectedSignal.signal}</p>
            <p className="text-sm">Confidence: {selectedSignal.confidence}%</p>
            <p className="text-sm">EV: {selectedSignal.expected_value?.toFixed?.(2)}</p>
            <p className="text-sm">Prob: {(selectedSignal.estimated_probability * 100)?.toFixed?.(1)}%</p>
            <p className="text-sm">Stake: {selectedSignal.suggested_stake}</p>
            <p className="text-sm">Risk: {selectedSignal.risk_level}</p>
            <p className="text-sm">Reasoning: {selectedSignal.reasoning}</p>
            {selectedSignal.sources?.length ? (
              <div className="text-xs text-muted">
                Sources:
                <ul className="list-disc ml-4">
                  {selectedSignal.sources.map((u: string) => (
                    <li key={u}>{u}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </Modal>

      <Modal
        open={!!selectedMarket}
        onClose={() => setSelectedMarket(null)}
        title={selectedMarket?.title}
      >
        {selectedMarket && (
          <div className="space-y-2">
            <p className="text-sm text-muted">Category: {selectedMarket.category}</p>
            <p className="text-sm">Liquidity: {selectedMarket.liquidity}</p>
            <p className="text-sm">Volume: {selectedMarket.totalVolume}</p>
            {selectedMarket.markets?.[0] && (
              <div className="text-sm">
                <p>YES: {selectedMarket.markets[0].outcome1Price}</p>
                <p>NO: {selectedMarket.markets[0].outcome2Price}</p>
              </div>
            )}
            {selectedMarket.description && <p className="text-sm">{selectedMarket.description}</p>}
          </div>
        )}
      </Modal>

      <Modal
        open={!!selectedActivity}
        onClose={() => setSelectedActivity(null)}
        title={selectedActivity?.marketTitle || selectedActivity?.eventTitle}
      >
        {selectedActivity && (
          <div className="space-y-2">
            <p className="text-sm text-muted">Type: {selectedActivity.type}</p>
            <p className="text-sm">Amount: {selectedActivity.amount}</p>
            <p className="text-sm">Size: {selectedActivity.size}</p>
            <p className="text-sm">Price: {selectedActivity.price}</p>
            <p className="text-sm">Status: {selectedActivity.status}</p>
            <p className="text-sm">Outcome: {selectedActivity.outcome}</p>
            {selectedActivity.payout && <p className="text-sm">Payout: {selectedActivity.payout}</p>}
            <p className="text-sm text-muted">
              {selectedActivity.createdAt ? new Date(selectedActivity.createdAt).toLocaleString() : ""}
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
