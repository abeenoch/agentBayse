import { useState, useEffect } from "react";
import { useAgentConfig, useSaveAgentConfig } from "../hooks/useAgentConfig";

export function Settings() {
  const { data: config, isLoading } = useAgentConfig();
  const save = useSaveAgentConfig();
  const [form, setForm] = useState({
    auto_trade: true,
    max_open_positions: 3,
    balance_floor: 0,
    min_confidence: 65,
    balance_reserve_pct: 0.30,
  });

  useEffect(() => {
    if (config) {
      setForm({
        auto_trade: config.auto_trade,
        max_open_positions: config.max_open_positions,
        balance_floor: config.balance_floor,
        min_confidence: config.min_confidence ?? 65,
        balance_reserve_pct: config.balance_reserve_pct ?? 0.30,
      });
    }
  }, [config]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await save.mutateAsync({
      auto_trade: form.auto_trade,
      max_open_positions: Number(form.max_open_positions),
      balance_floor: Number(form.balance_floor),
      min_confidence: Number(form.min_confidence),
      balance_reserve_pct: Number(form.balance_reserve_pct),
    });
  };

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm text-muted">Agent</p>
        <h1 className="text-2xl font-semibold">Settings</h1>
      </header>

      {isLoading && <p className="text-muted">Loading...</p>}

      <form className="space-y-4" onSubmit={onSubmit}>
        <div className="bg-surface border border-border rounded-xl p-4 space-y-4">

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={form.auto_trade}
              onChange={(e) => setForm((f) => ({ ...f, auto_trade: e.target.checked }))}
              className="w-4 h-4"
            />
            <div>
              <p className="font-medium">Autonomous trading</p>
              <p className="text-xs text-muted">Agent places bets automatically when signals pass all checks.</p>
            </div>
          </label>

          <label className="text-sm flex flex-col gap-1">
            <span className="font-medium">Max simultaneous bets</span>
            <input
              type="number"
              min={1}
              max={10}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2 w-32"
              value={form.max_open_positions}
              onChange={(e) => setForm((f) => ({ ...f, max_open_positions: Number(e.target.value) }))}
            />
            <span className="text-xs text-muted">How many open bets allowed at once. Recommended: 3.</span>
          </label>

          <label className="text-sm flex flex-col gap-1">
            <span className="font-medium">Minimum confidence to trade (%)</span>
            <input
              type="number"
              min={0}
              max={100}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2 w-32"
              value={form.min_confidence}
              onChange={(e) => setForm((f) => ({ ...f, min_confidence: Number(e.target.value) }))}
            />
            <span className="text-xs text-muted">Signals below this are blocked. Recommended: 65.</span>
          </label>

          <label className="text-sm flex flex-col gap-1">
            <span className="font-medium">Balance reserve (%)</span>
            <input
              type="number"
              min={0}
              max={0.9}
              step={0.05}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2 w-32"
              value={form.balance_reserve_pct}
              onChange={(e) => setForm((f) => ({ ...f, balance_reserve_pct: Number(e.target.value) }))}
            />
            <span className="text-xs text-muted">Fraction of wallet kept untouched. 0.30 = keep 30% back.</span>
          </label>

          <label className="text-sm flex flex-col gap-1">
            <span className="font-medium">Balance floor (₦)</span>
            <input
              type="number"
              min={0}
              className="bg-[#0F1016] border border-border rounded-lg px-3 py-2 w-32"
              value={form.balance_floor}
              onChange={(e) => setForm((f) => ({ ...f, balance_floor: Number(e.target.value) }))}
            />
            <span className="text-xs text-muted">Stop all trading if wallet drops below this amount.</span>
          </label>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="px-4 py-2 rounded-lg bg-primary text-bg font-semibold hover:bg-primary/90"
            disabled={save.isPending}
          >
            {save.isPending ? "Saving..." : "Save settings"}
          </button>
          {save.isSuccess && <span className="text-secondary text-sm">Saved ✓</span>}
        </div>
      </form>
    </div>
  );
}
