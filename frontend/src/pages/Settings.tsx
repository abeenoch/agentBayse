import { useState, useEffect } from "react";
import { useAgentConfig, useSaveAgentConfig } from "../hooks/useAgentConfig";

export function Settings() {
  const { data: config, isLoading } = useAgentConfig();
  const save = useSaveAgentConfig();
  const [form, setForm] = useState({
    auto_trade: true,
    categories: "",
    max_open_positions: 2,
    balance_floor: 0,
    min_confidence: 40,
  });

  useEffect(() => {
    if (config) {
      setForm({
        auto_trade: config.auto_trade,
        categories: (config.categories || []).join(","),
        max_open_positions: config.max_open_positions,
        balance_floor: config.balance_floor,
        min_confidence: config.min_confidence ?? 40,
      });
    }
  }, [config]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      auto_trade: form.auto_trade,
      categories: form.categories.split(",").map((c) => c.trim()).filter(Boolean),
      max_open_positions: Number(form.max_open_positions),
      balance_floor: Number(form.balance_floor),
      min_confidence: Number(form.min_confidence),
    };
    await save.mutateAsync(payload);
  };

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm text-muted">Agent</p>
        <h1 className="text-2xl font-semibold">Settings</h1>
      </header>

      {isLoading && <p className="text-muted">Loading...</p>}

      <form className="space-y-4" onSubmit={onSubmit}>
        <div className="bg-surface border border-border rounded-xl p-4 space-y-3">
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={form.auto_trade}
              onChange={(e) => setForm((f) => ({ ...f, auto_trade: e.target.checked }))}
            />
            <span>Enable autonomous trading</span>
          </label>

          <div>
            <p className="text-sm text-muted mb-1">Categories to scan (comma separated, empty = all)</p>
            <input
              className="w-full bg-[#0F1016] border border-border rounded-lg px-3 py-2"
              value={form.categories}
              onChange={(e) => setForm((f) => ({ ...f, categories: e.target.value }))}
              placeholder="sports,crypto,politics"
            />
          </div>

          <label className="text-sm flex flex-col gap-1">
            Max open positions (simultaneous trades)
            <input
              type="number"
              min={1}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2"
              value={form.max_open_positions}
              onChange={(e) => setForm((f) => ({ ...f, max_open_positions: Number(e.target.value) }))}
            />
            <span className="text-xs text-muted">Limits how many bets can be live at once.</span>
          </label>

          <div>
            <p className="text-sm text-muted mb-1">Balance floor (stop trading below this)</p>
            <input
              type="number"
              min={0}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2"
              value={form.balance_floor}
              onChange={(e) => setForm((f) => ({ ...f, balance_floor: Number(e.target.value) }))}
            />
          </div>

          <label className="text-sm flex flex-col gap-1">
            Minimum confidence to trade (0–100)
            <input
              type="number"
              min={0}
              max={100}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2"
              value={form.min_confidence}
              onChange={(e) => setForm((f) => ({ ...f, min_confidence: Number(e.target.value) }))}
            />
            <span className="text-xs text-muted">Signals below this confidence are blocked.</span>
          </label>
        </div>

        <button
          type="submit"
          className="px-4 py-2 rounded-lg bg-primary text-bg font-semibold hover:bg-primary/90"
          disabled={save.isPending}
        >
          {save.isPending ? "Saving..." : "Save settings"}
        </button>
        {save.isSuccess && <span className="text-secondary text-sm ml-2">Saved</span>}

        <div className="mt-4 text-sm text-muted bg-surface border border-border rounded-xl p-4">
          <p className="font-semibold text-foreground mb-1">Watchlist focus</p>
          <p>Agent scans only: BTC 5-minute markets, USD→NGN & GBP→NGN FX markets, and temperature markets in Nigeria. Other events are skipped.</p>
        </div>
      </form>
    </div>
  );
}
