import { useState, useEffect } from "react";
import { useAgentConfig, useSaveAgentConfig } from "../hooks/useAgentConfig";

export function Settings() {
  const { data: config, isLoading } = useAgentConfig();
  const save = useSaveAgentConfig();
  const [form, setForm] = useState({
    auto_trade: true,
    categories: "",
    max_trades_per_hour: 10,
    max_trades_per_day: 50,
    balance_floor: 0,
  });

  useEffect(() => {
    if (config) {
      setForm({
        auto_trade: config.auto_trade,
        categories: (config.categories || []).join(","),
        max_trades_per_hour: config.max_trades_per_hour,
        max_trades_per_day: config.max_trades_per_day,
        balance_floor: config.balance_floor,
      });
    }
  }, [config]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      auto_trade: form.auto_trade,
      categories: form.categories
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean),
      max_trades_per_hour: Number(form.max_trades_per_hour),
      max_trades_per_day: Number(form.max_trades_per_day),
      balance_floor: Number(form.balance_floor),
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

          <div className="grid md:grid-cols-2 gap-3">
            <label className="text-sm flex flex-col gap-1">
              Max trades per hour
              <input
                type="number"
                min={1}
                className="bg-[#0F1016] border border-border rounded-lg px-3 py-2"
                value={form.max_trades_per_hour}
                onChange={(e) => setForm((f) => ({ ...f, max_trades_per_hour: Number(e.target.value) }))}
              />
            </label>
            <label className="text-sm flex flex-col gap-1">
              Max trades per day
              <input
                type="number"
                min={1}
                className="bg-[#0F1016] border border-border rounded-lg px-3 py-2"
                value={form.max_trades_per_day}
                onChange={(e) => setForm((f) => ({ ...f, max_trades_per_day: Number(e.target.value) }))}
              />
            </label>
          </div>

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
        </div>

        <button
          type="submit"
          className="px-4 py-2 rounded-lg bg-primary text-bg font-semibold hover:bg-primary/90"
          disabled={save.isLoading}
        >
          {save.isLoading ? "Saving..." : "Save settings"}
        </button>
        {save.isSuccess && <span className="text-secondary text-sm ml-2">Saved</span>}
      </form>
    </div>
  );
}
