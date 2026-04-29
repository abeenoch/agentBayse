import { useMarkets } from "../hooks/useMarkets";
import { useState } from "react";
import { Modal } from "../components/Modal";
import { useTicker } from "../hooks/useTicker";
import { useOrderBook } from "../hooks/useOrderBook";
import { usePriceHistory } from "../hooks/usePriceHistory";
import { useTrades } from "../hooks/useTrades";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer, BarChart, Bar } from "recharts";
import { useMemo } from "react";

export function Markets() {
  const [page, setPage] = useState(1);
  const [category, setCategory] = useState<string | undefined>(undefined);
  const { data, isLoading } = useMarkets(30, page, category);
  const markets = data?.events || [];
  const [selected, setSelected] = useState<any | null>(null);
  const firstMarket = selected?.markets?.[0];
  const ticker = useTicker(firstMarket?.id);
  const orderbook = useOrderBook(
    firstMarket ? [firstMarket.outcome1Id, firstMarket.outcome2Id].filter(Boolean) : undefined
  );
  const priceHistory = usePriceHistory(selected?.id, firstMarket?.id);
  const trades = useTrades(firstMarket?.id, 10);

  const pricePoints = useMemo(() => {
    const raw = priceHistory.data ? (Object.values(priceHistory.data)[0] as any[]) : [];
    return (raw || []).map((p, idx) => ({
      idx,
      price: p.price ?? p.value ?? 0,
      ts: p.timestamp,
    })).filter((p) => p.price !== 0);
  }, [priceHistory.data]);

  const orderBooks = useMemo(() => {
    if (!orderbook.data) return [];
    if (Array.isArray(orderbook.data)) return orderbook.data;
    if (orderbook.data.orderBooks && Array.isArray(orderbook.data.orderBooks)) return orderbook.data.orderBooks;
    return [];
  }, [orderbook.data]);

  const bidSum =
    orderBooks?.[0]?.bids?.slice(0, 5).reduce((acc: number, b: any) => acc + (b.total ?? 0), 0) || 0;
  const askSum =
    orderBooks?.[0]?.asks?.slice(0, 5).reduce((acc: number, b: any) => acc + (b.total ?? 0), 0) || 0;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-sm text-muted">Browse</p>
          <h1 className="text-2xl font-semibold">Markets</h1>
        </div>
        <div className="flex gap-2">
          {["", "crypto", "finance", "sports"].map((cat) => (
            <button
              key={cat}
              onClick={() => { setCategory(cat || undefined); setPage(1); }}
              className={`px-3 py-1 rounded-lg text-sm border transition ${
                (category ?? "") === cat
                  ? "border-primary bg-primary/20 text-primary"
                  : "border-border text-muted hover:text-text"
              }`}
            >
              {cat || "All"}
            </button>
          ))}
        </div>
      </header>
      {isLoading && <p className="text-muted">Loading...</p>}
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(markets || []).map((evt: any) => (
          <button
            key={evt.id}
            onClick={() => setSelected(evt)}
            className="text-left bg-surface border border-border rounded-xl p-4 space-y-2 hover:border-primary/60 transition"
          >
            <p className="text-xs uppercase tracking-wide text-muted">{evt.category}</p>
            <h3 className="font-semibold leading-snug">{evt.title}</h3>
            <p className="text-sm text-muted line-clamp-2">{evt.description}</p>
            <div className="flex justify-between text-sm font-mono">
              <span className="text-secondary">YES {evt.markets?.[0]?.outcome1Price ?? "--"}</span>
              <span className="text-danger">NO {evt.markets?.[0]?.outcome2Price ?? "--"}</span>
            </div>
          </button>
        ))}
      </div>
      {!isLoading && !(markets?.length) && <p className="text-muted text-sm">No markets found.</p>}

      <div className="flex items-center justify-between mt-4">
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page === 1}
          className="px-3 py-1 rounded-lg border border-border text-sm disabled:opacity-50"
        >
          Previous
        </button>
        <p className="text-xs text-muted">
          Page {page} / {data?.pagination?.lastPage ?? "?"}
        </p>
        <button
          onClick={() => setPage((p) => p + 1)}
          disabled={data?.pagination && page >= (data.pagination.lastPage || page)}
          className="px-3 py-1 rounded-lg border border-border text-sm disabled:opacity-50"
        >
          Next
        </button>
      </div>

      <Modal open={!!selected} onClose={() => setSelected(null)} title={selected?.title}>
        {selected && (
          <div className="space-y-3">
            <p className="text-sm text-muted">Category: {selected.category}</p>
            <p className="text-sm">Liquidity: {selected.liquidity}</p>
            <p className="text-sm">Volume: {selected.totalVolume}</p>
            <p className="text-sm">Description: {selected.description}</p>
            <div className="space-y-1">
              {(selected.markets || []).map((m: any) => (
                <div key={m.id} className="border border-border rounded p-2">
                  <p className="font-semibold">{m.title}</p>
                  <p className="text-sm">YES {m.outcome1Price} | NO {m.outcome2Price}</p>
                  {m.rules && <p className="text-xs text-muted">Rules: {m.rules}</p>}
                </div>
              ))}
            </div>
            {ticker.data && (
              <div className="text-sm border border-border rounded p-2">
                <p className="font-semibold">Ticker</p>
                <p>
                  Last: {ticker.data.lastPrice ?? "--"} | Bid: {ticker.data.bestBid ?? "--"} | Ask:{" "}
                  {ticker.data.bestAsk ?? "--"}
                </p>
                <p>24h Vol: {ticker.data.volume24h ?? "--"}</p>
              </div>
            )}
            {!ticker.data && <p className="text-sm text-muted">No ticker data available.</p>}
            {orderBooks.length > 0 ? (
              <div className="text-sm border border-border rounded p-2">
                <p className="font-semibold mb-2">Order Book (depth 5)</p>
                <div className="h-28">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={[{ bids: bidSum, asks: askSum }]}>
                      <CartesianGrid stroke="#1E2028" />
                      <Bar dataKey="bids" fill="#00D9A5" />
                      <Bar dataKey="asks" fill="#FF4F4F" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted">No order book data available.</p>
            )}
            {trades.data?.length ? (
              <div className="text-sm border border-border rounded p-2 space-y-1">
                <p className="font-semibold">Recent Trades</p>
                {trades.data.slice(0, 5).map((t: any) => (
                  <p key={t.id} className="text-xs">
                    {t.side} {t.outcome} @ {t.price} ({t.quantity}) – {new Date(t.timestamp).toLocaleTimeString()}
                  </p>
                ))}
              </div>
            ) : null}
            {pricePoints.length > 0 ? (
              <div className="text-sm border border-border rounded p-2">
                <p className="font-semibold mb-2">Price History (1W, YES)</p>
                <div className="h-32">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={pricePoints} margin={{ top: 5, right: 5, left: -10, bottom: 0 }}>
                      <CartesianGrid stroke="#1E2028" />
                      <XAxis dataKey="idx" hide />
                      <YAxis domain={[0, 1]} tickFormatter={(v) => (v * 100).toFixed(0) + "%"} />
                      <Area type="monotone" dataKey="price" stroke="#5B4FFF" fill="#5B4FFF33" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted">No price history available (Bayse returned no data).</p>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
