import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAgentConfig } from "../hooks/useAgentConfig";
import { useSignals } from "../hooks/useSignals";

type TraceResponse = {
  market_id?: string;
  trade?: any;
  signal?: any;
  feature_snapshot?: any;
  bayes_state?: any;
  live_training_run?: any;
  resolved_live_training_state_key?: string;
  config?: any;
  live_diagnostics?: any[];
};

const fmtPct = (value: any) => `${(Number(value || 0) * 100).toFixed(1)}%`;

export function Bayes() {
  const { data: signalsResp, isLoading: signalsLoading } = useSignals(50, 1, undefined, true);
  const { data: agentConfig } = useAgentConfig();
  const queryClient = useQueryClient();
  const [selectedMarketId, setSelectedMarketId] = useState<string | null>(null);

  const { data: report } = useQuery({
    queryKey: ["bayes-report"],
    queryFn: async () => {
      const { data } = await api.get("/agent/bayes/report");
      return data;
    },
    refetchInterval: 15_000,
  });

  const { data: liveTraining } = useQuery({
    queryKey: ["bayes-live-training"],
    queryFn: async () => {
      const { data } = await api.get("/agent/bayes/live-training");
      return data;
    },
    refetchInterval: 15_000,
  });

  const activeStateKey =
    liveTraining?.resolved_state_key ||
    liveTraining?.live_training_run?.state_key ||
    report?.resolved_live_training_state_key ||
    report?.live_training_run?.state_key ||
    report?.bayes_state?.state_key ||
    agentConfig?.bayes_state_key ||
    "default";

  const { data: yesNoAudit } = useQuery({
    queryKey: ["bayes-audit", activeStateKey],
    queryFn: async () => {
      const { data } = await api.get("/agent/bayes/audit", { params: { state_key: activeStateKey } });
      return data;
    },
    enabled: !!activeStateKey,
    refetchInterval: 60_000,
  });

  const { data: calibration } = useQuery({
    queryKey: ["bayes-calibration", activeStateKey],
    queryFn: async () => {
      const { data } = await api.get("/agent/bayes/calibration", { params: { state_key: activeStateKey } });
      return data;
    },
    enabled: !!activeStateKey,
    refetchInterval: 60_000,
  });

  const { data: latestTraining } = useQuery({
    queryKey: ["bayes-training-latest", activeStateKey],
    queryFn: async () => {
      const { data } = await api.get("/agent/bayes/train/latest", { params: { state_key: activeStateKey } });
      return data;
    },
    enabled: !!activeStateKey,
    refetchInterval: 15_000,
  });

  const trainMutation = useMutation({
    mutationFn: async () => {
      const { data } = await api.post("/agent/bayes/train", null, { params: { state_key: activeStateKey } });
      return data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["bayes-live-training"] });
      await queryClient.invalidateQueries({ queryKey: ["bayes-training-latest", activeStateKey] });
      await queryClient.invalidateQueries({ queryKey: ["bayes-calibration", activeStateKey] });
      await queryClient.invalidateQueries({ queryKey: ["bayes-audit", activeStateKey] });
      await queryClient.invalidateQueries({ queryKey: ["bayes-report"] });
    },
  });

  const signals = signalsResp?.signals || [];
  const selectedSignal = useMemo(
    () => signals.find((s: any) => s.market_id === selectedMarketId) || signals[0] || null,
    [signals, selectedMarketId]
  );

  useEffect(() => {
    if (!selectedMarketId && signals[0]?.market_id) {
      setSelectedMarketId(signals[0].market_id);
    }
  }, [signals, selectedMarketId]);

  useEffect(() => {
    if (signals[0]?.market_id) {
      setSelectedMarketId((current) => current || signals[0].market_id);
    }
  }, [signals]);

  const { data: trace, isFetching: traceLoading } = useQuery<TraceResponse>({
    queryKey: ["bayes-trace", selectedMarketId],
    queryFn: async () => {
      if (!selectedMarketId) return null as any;
      const { data } = await api.get("/agent/trades/trace", {
        params: { market_id: selectedMarketId },
      });
      return data;
    },
    enabled: !!selectedMarketId,
    refetchInterval: 20_000,
  });

  const state = report?.bayes_state;
  const latestSignal = signals[0] || null;
  const yesAudit = yesNoAudit?.yes || null;
  const noAudit = yesNoAudit?.no || null;
  const calibrationOverall = calibration?.overall || null;
  const calibrationYes = calibration?.yes || null;
  const calibrationNo = calibration?.no || null;
  const activeTrainingRun = liveTraining?.live_training_run || report?.live_training_run || latestTraining || null;

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <div>
          <p className="text-sm text-muted">Inspector</p>
          <h1 className="text-2xl font-semibold">Bayes Report</h1>
          <p className="text-sm text-muted mt-1">
            The page auto-loads the latest bet. Click any recent bet to inspect its prior, posterior, scope, and settlement trace.
          </p>
        </div>
        <div className="sm:text-right">
          <p className="text-xs text-muted">Current model</p>
          <p className="font-semibold">
            {activeTrainingRun ? `${activeTrainingRun.model_version} - ${activeStateKey}` : "Loading..."}
          </p>
          <p className="text-xs text-muted">
            {activeTrainingRun?.trained_at
              ? `Trained ${new Date(activeTrainingRun.trained_at).toLocaleString()}`
              : "Waiting for training run"}
          </p>
        </div>
      </header>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Baseline Prior</p>
          <p className="text-2xl font-mono font-semibold">{state ? fmtPct(state.prior_json?.prior_yes) : "—"}</p>
          <p className="text-xs text-muted">
            YES / NO before evidence
          </p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Live Training</p>
          <p className="text-2xl font-mono font-semibold">
            {activeTrainingRun ? activeTrainingRun.model_version : "—"}
          </p>
          <p className="text-xs text-muted">
            {activeTrainingRun
              ? `${activeTrainingRun.sample_size ?? 0} samples - ${activeTrainingRun.state_key}`
              : "No training run yet"}
          </p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Calibration</p>
          <p className="text-2xl font-mono font-semibold">
            {calibrationOverall?.metrics ? `${Number(calibrationOverall.metrics.brier || 0).toFixed(3)}` : "—"}
          </p>
          <p className="text-xs text-muted">
            {calibrationOverall?.metrics
              ? `Brier / log loss: ${Number(calibrationOverall.metrics.brier || 0).toFixed(3)} / ${Number(calibrationOverall.metrics.log_loss || 0).toFixed(3)}`
              : "Waiting for calibration"}
          </p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">Latest Bet</p>
          <p className="text-sm font-semibold truncate">{latestSignal?.market_name || "No signal yet"}</p>
          <p className="text-xs text-muted">
            {latestSignal ? `${latestSignal.signal_type || latestSignal.signal} - ${latestSignal.bayes_state_key || "default"}` : "Waiting for a signal"}
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="bg-surface border border-border rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">Recent Bets</h2>
              <p className="text-xs text-muted">Select a bet to inspect the full pipeline and compare it to the live model.</p>
            </div>
            <div className="text-right">
              <button
                onClick={() => trainMutation.mutate()}
                className="px-3 py-1 rounded-lg border border-border text-xs hover:border-primary/60"
                disabled={trainMutation.isPending}
              >
                {trainMutation.isPending ? "Training..." : "Retrain"}
              </button>
              <p className="text-xs text-muted mt-1">{traceLoading ? "Loading trace..." : ""}</p>
            </div>
          </div>

          <div className="space-y-2 max-h-[32rem] overflow-y-auto pr-1">
            {signalsLoading && <p className="text-sm text-muted">Loading signals...</p>}
            {signals.map((s: any) => {
              const sig = s.signal_type || s.signal;
              const active = s.market_id === selectedMarketId;
              return (
                <button
                  key={s.id}
                  onClick={() => setSelectedMarketId(s.market_id)}
                  className={`w-full text-left rounded-lg border p-3 transition ${
                    active ? "border-primary bg-primary/10" : "border-border bg-[#0F1016] hover:border-primary/60"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-muted truncate">{s.market_name}</p>
                      <p className="font-semibold text-sm">{sig}</p>
                    </div>
                    <div className="text-right text-xs text-muted shrink-0">
                      <p>{s.bayes_state_key || "default"}</p>
                      <p>Conf {s.confidence}%</p>
                    </div>
                  </div>
                  <p className="text-xs text-muted mt-2 line-clamp-2">{s.reasoning}</p>
                  {s.created_at && (
                    <p className="text-[11px] text-muted mt-1">{new Date(s.created_at).toLocaleString()}</p>
                  )}
                </button>
              );
            })}
            {!signalsLoading && !signals.length && <p className="text-sm text-muted">No signals yet.</p>}
          </div>
        </section>

        <section className="bg-surface border border-border rounded-xl p-4 space-y-3">
          <h2 className="font-semibold">Trace Result</h2>
          {!selectedSignal ? (
            <p className="text-sm text-muted">Click a recent bet to inspect it.</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                <p className="text-xs text-muted">Selected Bet</p>
                <p className="font-semibold truncate">{selectedSignal.market_name}</p>
                <p className="text-xs text-muted">
                  {selectedSignal.signal_type || selectedSignal.signal} - scope {selectedSignal.bayes_state_key || "default"}
                </p>
                <p className="text-xs text-muted">Reasoning: {selectedSignal.reasoning || "—"}</p>
                <p className="text-xs text-muted">{traceLoading ? "Loading trace..." : "Trace loaded"}</p>
              </div>

              {trace && (
                <>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                      <p className="text-xs text-muted">Signal</p>
                      <p className="font-semibold">{trace.signal?.market_name || "—"}</p>
                      <p className="text-xs text-muted">Scope: {trace.signal?.bayes_state_key || "default"}</p>
                      <p className="text-xs text-muted">Reasoning: {trace.signal?.reasoning || "—"}</p>
                    </div>
                    <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                      <p className="text-xs text-muted">Trade</p>
                      <p className="font-semibold">{trace.trade?.market_name || "—"}</p>
                      <p className="text-xs text-muted">
                        Status: {trace.trade?.status || "—"} / {trace.trade?.resolution || "—"}
                      </p>
                      <p className="text-xs text-muted">Scope: {trace.trade?.bayes_state_key || "default"}</p>
                      <p className="text-xs text-muted">P&amp;L: {trace.trade?.pnl != null ? trace.trade.pnl : "—"}</p>
                    </div>
                  </div>

                  <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                    <p className="text-xs text-muted">Feature Snapshot</p>
                    <p className="text-sm font-semibold truncate">{trace.feature_snapshot?.market_name || "—"}</p>
                    <p className="text-xs text-muted">Posterior action: {trace.feature_snapshot?.posterior_action || "—"}</p>
                    <p className="text-xs text-muted">Posterior YES: {fmtPct(trace.feature_snapshot?.posterior_yes)}</p>
                    <p className="text-xs text-muted">Snapshot reasoning is on the signal record.</p>
                  </div>

                  <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                    <p className="text-xs text-muted">Bayes State</p>
                    <p className="text-sm font-semibold">{trace.bayes_state?.state_key || "—"}</p>
                    <p className="text-xs text-muted">Prior YES: {fmtPct(trace.bayes_state?.prior_json?.prior_yes)}</p>
                    <p className="text-xs text-muted">
                      Updates: {trace.bayes_state?.yes_updates ?? "—"} yes / {trace.bayes_state?.no_updates ?? "—"} no
                    </p>
                  </div>

                  <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                    <p className="text-xs text-muted">Live Model</p>
                    <p className="text-sm font-semibold">
                      {trace.live_training_run?.model_version || activeTrainingRun?.model_version || "—"}
                    </p>
                    <p className="text-xs text-muted">
                      Resolved state: {trace.resolved_live_training_state_key || activeTrainingRun?.state_key || "—"}
                    </p>
                    <p className="text-xs text-muted">
                      Samples: {trace.live_training_run?.sample_size ?? activeTrainingRun?.sample_size ?? "—"}
                    </p>
                  </div>

                  <div className="rounded-lg border border-border bg-[#0F1016] p-3">
                    <p className="text-xs text-muted">Diagnostics</p>
                    {trace.live_diagnostics?.length ? (
                      <div className="space-y-2 mt-2">
                        {trace.live_diagnostics.slice(0, 3).map((row: any) => (
                          <div key={row.trade_id} className="text-xs border border-border rounded-lg p-2">
                            <p className="font-semibold truncate">{row.market_name}</p>
                            <p className="text-muted">Match: {row.bayse_match?.matched ? row.bayse_match?.match_type : "no"}</p>
                            <p className="text-muted">Status: {row.status} / {row.resolution || "—"}</p>
                            <p className="text-muted">Fetch error: {row.bayse_fetch_error || "none"}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-muted">
                        Diagnostics are hidden by default. Use the backend trace endpoint with include_diagnostics=true when you need them.
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </section>
      </div>

      {yesNoAudit ? (
        <section className="bg-surface border border-border rounded-xl p-4 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">YES / NO Audit</h2>
              <p className="text-xs text-muted">
                Actionable signal mix and realized outcomes for {yesNoAudit.state_key}. This shows whether the skew is coming from the emitted signal side or from execution filters.
              </p>
            </div>
            <div className="text-right text-xs text-muted">
              <p>{yesNoAudit.total_signals ?? 0} actionable signals</p>
              <p>
                Bias: {yesNoAudit.side_bias || "BALANCED"} - Resolved {yesNoAudit.resolved_trades ?? 0}
              </p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-4">
            <div className="rounded-lg border border-border bg-[#0F1016] p-3">
              <p className="text-xs text-muted">YES share</p>
              <p className="text-2xl font-mono font-semibold">{((yesNoAudit.signal_share?.yes || 0) * 100).toFixed(1)}%</p>
            </div>
            <div className="rounded-lg border border-border bg-[#0F1016] p-3">
              <p className="text-xs text-muted">NO share</p>
              <p className="text-2xl font-mono font-semibold">{((yesNoAudit.signal_share?.no || 0) * 100).toFixed(1)}%</p>
            </div>
            <div className="rounded-lg border border-border bg-[#0F1016] p-3">
              <p className="text-xs text-muted">Wins / losses</p>
              <p className="text-2xl font-mono font-semibold">
                {yesNoAudit.wins ?? 0} / {yesNoAudit.losses ?? 0}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-[#0F1016] p-3">
              <p className="text-xs text-muted">Total P&L</p>
              <p className={`text-2xl font-mono font-semibold ${(Number(yesNoAudit.total_pnl || 0) >= 0) ? "text-secondary" : "text-danger"}`}>
                {Number(yesNoAudit.total_pnl || 0).toFixed(2)}
              </p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {[
              { label: "YES", data: yesAudit },
              { label: "NO", data: noAudit },
            ].map((side) => {
              const data = side.data || {};
              const count = Number(data.count || 0);
              const resolved = Number(data.resolved_count || 0);
              const barWidth = `${Math.max(8, Math.min(100, (count / Math.max(Number(yesNoAudit.total_signals || count || 1), 1)) * 100))}%`;
              return (
                <div key={side.label} className="rounded-lg border border-border bg-[#0F1016] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="font-semibold">{side.label}</p>
                    <p className="text-xs text-muted">{count} signals</p>
                  </div>
                  <div className="h-2 rounded-full bg-border overflow-hidden">
                    <div className={`h-full rounded-full ${side.label === "NO" ? "bg-danger/80" : "bg-secondary/80"}`} style={{ width: barWidth }} />
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded-md border border-border p-2">
                      <p className="text-muted">Resolved</p>
                      <p className="font-mono font-semibold">{resolved}</p>
                    </div>
                    <div className="rounded-md border border-border p-2">
                      <p className="text-muted">Win rate</p>
                      <p className="font-mono font-semibold">{(Number(data.win_rate || 0) * 100).toFixed(1)}%</p>
                    </div>
                    <div className="rounded-md border border-border p-2">
                      <p className="text-muted">Avg confidence</p>
                      <p className="font-mono font-semibold">{Number(data.avg_confidence || 0).toFixed(1)}</p>
                    </div>
                    <div className="rounded-md border border-border p-2">
                      <p className="text-muted">Avg EV</p>
                      <p className="font-mono font-semibold">{Number(data.avg_expected_value || 0).toFixed(2)}</p>
                    </div>
                    <div className="rounded-md border border-border p-2">
                      <p className="text-muted">Avg edge</p>
                      <p className="font-mono font-semibold">{Number(data.avg_edge || 0).toFixed(3)}</p>
                    </div>
                    <div className="rounded-md border border-border p-2">
                      <p className="text-muted">Avg stake</p>
                      <p className="font-mono font-semibold">{Number(data.avg_stake || 0).toFixed(2)}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="rounded-lg border border-border bg-[#0F1016] p-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="font-semibold">Calibration</p>
              <p className="text-xs text-muted">Model: {activeTrainingRun?.model_version || "unknown"}</p>
            </div>
            <div className="grid gap-3 md:grid-cols-2 text-sm">
              <div className="rounded-md border border-border p-2">
                <p className="text-xs text-muted">Overall</p>
                <p>Brier: {calibrationOverall?.metrics ? Number(calibrationOverall.metrics.brier || 0).toFixed(4) : "—"}</p>
                <p>Log loss: {calibrationOverall?.metrics ? Number(calibrationOverall.metrics.log_loss || 0).toFixed(4) : "—"}</p>
                <p>Accuracy: {calibrationOverall?.metrics ? Number(calibrationOverall.metrics.accuracy || 0).toFixed(4) : "—"}</p>
              </div>
              <div className="rounded-md border border-border p-2">
                <p className="text-xs text-muted">Buckets</p>
                <p className="text-xs text-muted">YES buckets: {calibrationYes?.bins?.length ?? 0}</p>
                <p className="text-xs text-muted">NO buckets: {calibrationNo?.bins?.length ?? 0}</p>
                <p className="text-xs text-muted">Training samples: {activeTrainingRun?.sample_size ?? "—"}</p>
                <p className="text-xs text-muted">Key: {activeStateKey}</p>
              </div>
            </div>
          </div>
        </section>
      ) : (
        <section className="bg-surface border border-border rounded-xl p-4">
          <p className="text-sm text-muted">No audit data yet for the active model.</p>
        </section>
      )}
    </div>
  );
}
