"use client";

import { useCallback, useEffect, useState } from "react";
import type { AuthUser, PortfolioAnalysis, SyncedPosition } from "@/types";
import {
  ApiError,
  confirmPortfolioSync,
  fetchPortfolioAnalysis,
  previewPortfolioSync,
} from "@/lib/api";

interface PortfolioPanelProps {
  onSelectTicker?: (ticker: string) => void;
  user?: AuthUser | null;
}

function riskStyles(level: string) {
  if (level === "HIGH")
    return { text: "text-red-400", bg: "bg-red-500", label: "high" };
  if (level === "LOW")
    return { text: "text-emerald-400", bg: "bg-emerald-500", label: "low" };
  return { text: "text-amber-400", bg: "bg-amber-500", label: "med" };
}

function RiskBar({ riskMix, total }: { riskMix: Record<string, number>; total: number }) {
  if (total === 0) return null;
  const segments = [
    { key: "LOW", color: "bg-emerald-500" },
    { key: "MEDIUM", color: "bg-amber-500" },
    { key: "HIGH", color: "bg-red-500" },
  ];
  return (
    <div className="mt-3">
      <div className="flex h-2 overflow-hidden rounded-full bg-white/5">
        {segments.map(({ key, color }) => {
          const count = riskMix[key] ?? 0;
          if (count === 0) return null;
          return (
            <div
              key={key}
              className={`${color} transition-all`}
              style={{ width: `${(count / total) * 100}%` }}
              title={`${key}: ${count}`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {Object.entries(riskMix).map(([level, count]) => {
          const s = riskStyles(level);
          return (
            <span
              key={level}
              className={`rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium uppercase ${s.text}`}
            >
              {s.label} ×{count}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export default function PortfolioPanel({ onSelectTicker, user }: PortfolioPanelProps) {
  const [data, setData] = useState<PortfolioAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showSync, setShowSync] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncWarnings, setSyncWarnings] = useState<string[]>([]);
  const [previewPositions, setPreviewPositions] = useState<SyncedPosition[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchPortfolioAnalysis();
      setData(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load portfolio.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const totalHoldings = data?.holdings.length ?? 0;

  async function handlePreview(useOpenclaw: boolean) {
    setSyncLoading(true);
    setSyncError(null);
    setSyncWarnings([]);
    setPreviewPositions(null);
    setSaveSuccess(false);
    try {
      const result = await previewPortfolioSync({
        rawText: useOpenclaw ? undefined : pasteText,
        useOpenclaw,
      });
      setPreviewPositions(result.positions);
      setSyncWarnings(result.warnings);
      if (result.positions.length === 0) {
        setSyncError("No positions found. Try pasting a cleaner copy of the page.");
      }
    } catch (err) {
      setSyncError(
        err instanceof ApiError
          ? err.message
          : "Could not extract positions from that text."
      );
    } finally {
      setSyncLoading(false);
    }
  }

  async function handleConfirm() {
    if (!previewPositions || previewPositions.length === 0) return;
    setSaving(true);
    setSyncError(null);
    try {
      await confirmPortfolioSync(previewPositions);
      setSaveSuccess(true);
      setPreviewPositions(null);
      setPasteText("");
      await load();
    } catch (err) {
      setSyncError(
        err instanceof ApiError ? err.message : "Could not save positions."
      );
    } finally {
      setSaving(false);
    }
  }

  function updatePreviewField(
    index: number,
    field: "shares" | "avg_cost",
    value: string
  ) {
    setPreviewPositions((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      const parsed = value.trim() === "" ? null : Number(value);
      next[index] = { ...next[index], [field]: Number.isNaN(parsed as number) ? null : parsed };
      return next;
    });
  }

  function removePreviewRow(index: number) {
    setPreviewPositions((prev) => (prev ? prev.filter((_, i) => i !== index) : prev));
  }

  return (
    <div className="card-surface card-surface-hover p-5">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <h3 className="panel-title">portfolio basket</h3>
          <p className="mt-0.5 text-[11px] text-slate-600">
            watchlist basket · sync real broker positions when signed in
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {user && (
            <button
              type="button"
              onClick={() => {
                setShowSync((v) => !v);
                setSyncError(null);
                setSaveSuccess(false);
              }}
              className={`rounded-lg border px-2.5 py-1 text-xs transition ${
                showSync
                  ? "border-violet-500/50 bg-violet-500/15 text-violet-200"
                  : "border-white/[0.08] text-slate-400 hover:border-violet-500/30 hover:text-violet-300"
              }`}
            >
              sync from broker
            </button>
          )}
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="rounded-lg border border-white/[0.08] px-2.5 py-1 text-xs text-slate-400 transition hover:border-violet-500/30 hover:text-violet-300 disabled:opacity-50"
          >
            {loading ? "…" : "↻"}
          </button>
        </div>
      </div>

      {showSync && user && (
        <div className="mb-4 rounded-xl border border-violet-500/20 bg-violet-500/[0.04] p-4">
          <p className="text-xs leading-relaxed text-slate-400">
            Paste the text of your broker&apos;s positions page (select all,
            copy, paste below) to import real shares and cost basis. We never
            see your login — this only reads text you paste yourself.
          </p>

          {!previewPositions && (
            <>
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder="Paste your positions table here (ticker, shares, price, etc.)"
                rows={4}
                className="mt-3 w-full resize-none rounded-lg border border-white/[0.08] bg-[#0a0a0f]/60 px-3 py-2 text-xs text-slate-300 placeholder:text-slate-600 focus:border-violet-500/40 focus:outline-none"
              />
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => handlePreview(false)}
                  disabled={syncLoading || !pasteText.trim()}
                  className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-1.5 text-xs font-medium text-violet-200 transition hover:bg-violet-500/20 disabled:opacity-40"
                >
                  {syncLoading ? "extracting…" : "extract positions"}
                </button>
                <button
                  type="button"
                  onClick={() => handlePreview(true)}
                  disabled={syncLoading}
                  title="Requires OpenClaw configured with an existing-session browser attach"
                  className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-xs text-slate-400 transition hover:border-cyan-500/30 hover:text-cyan-300 disabled:opacity-40"
                >
                  try openclaw auto-snapshot
                </button>
              </div>
            </>
          )}

          {syncError && (
            <p className="mt-2 text-xs text-red-400" role="alert">
              {syncError}
            </p>
          )}
          {syncWarnings.length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {syncWarnings.map((w, i) => (
                <li key={i} className="text-[11px] text-amber-400/80">
                  ⚠ {w}
                </li>
              ))}
            </ul>
          )}
          {saveSuccess && (
            <p className="mt-2 text-xs text-emerald-400" role="status">
              Saved. Your portfolio basket now reflects these positions.
            </p>
          )}

          {previewPositions && previewPositions.length > 0 && (
            <>
              <p className="mt-3 text-[11px] uppercase tracking-wider text-slate-500">
                review before saving
              </p>
              <div className="mt-2 space-y-1.5">
                {previewPositions.map((p, i) => (
                  <div
                    key={`${p.ticker}-${i}`}
                    className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-xs"
                  >
                    <span className="w-14 font-display font-semibold text-white">
                      {p.ticker}
                    </span>
                    <input
                      type="number"
                      value={p.shares ?? ""}
                      onChange={(e) => updatePreviewField(i, "shares", e.target.value)}
                      placeholder="shares"
                      className="w-20 rounded border border-white/[0.08] bg-[#0a0a0f]/60 px-1.5 py-1 text-slate-300 focus:border-violet-500/40 focus:outline-none"
                    />
                    <input
                      type="number"
                      value={p.avg_cost ?? ""}
                      onChange={(e) => updatePreviewField(i, "avg_cost", e.target.value)}
                      placeholder="avg cost"
                      className="w-20 rounded border border-white/[0.08] bg-[#0a0a0f]/60 px-1.5 py-1 text-slate-300 focus:border-violet-500/40 focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={() => removePreviewRow(i)}
                      className="ml-auto text-slate-600 hover:text-red-400"
                      aria-label={`Remove ${p.ticker}`}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={handleConfirm}
                  disabled={saving}
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/20 disabled:opacity-40"
                >
                  {saving ? "saving…" : `save ${previewPositions.length} position${previewPositions.length !== 1 ? "s" : ""}`}
                </button>
                <button
                  type="button"
                  onClick={() => setPreviewPositions(null)}
                  className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-xs text-slate-400 transition hover:text-slate-200"
                >
                  discard
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {loading && !data && (
        <div className="space-y-2">
          <div className="skeleton-shimmer h-12 rounded-lg" />
          <div className="skeleton-shimmer h-8 rounded-lg" />
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      {data && (
        <>
          {totalHoldings > 0 ? (
            <>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-slate-600">
                    holdings
                  </p>
                  <p className="font-display mt-1 text-2xl font-bold text-white">
                    {totalHoldings}
                  </p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-slate-600">
                    {data.weighted_by_real_positions ? "total value" : "avg P/E"}
                  </p>
                  <p className="font-display mt-1 text-2xl font-bold text-white">
                    {data.weighted_by_real_positions
                      ? `$${data.total_market_value?.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                      : data.avg_pe ?? "-"}
                  </p>
                </div>
              </div>

              {data.weighted_by_real_positions && (
                <p className="mt-2 text-[10px] text-cyan-500/80">
                  weighted by real position sizes
                </p>
              )}

              {data.weakest_ticker && (
                <p className="mt-3 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-300/90">
                  weakest link:{" "}
                  <button
                    type="button"
                    onClick={() => onSelectTicker?.(data.weakest_ticker!)}
                    className="font-semibold underline decoration-red-500/40 hover:text-red-200"
                  >
                    {data.weakest_ticker}
                  </button>
                </p>
              )}

              <RiskBar riskMix={data.risk_mix} total={totalHoldings} />

              <ul className="mt-4 space-y-1.5">
                {data.holdings.map((h) => {
                  const s = riskStyles(h.risk_level);
                  return (
                    <li key={h.ticker}>
                      <button
                        type="button"
                        onClick={() => onSelectTicker?.(h.ticker)}
                        className="group flex w-full items-center justify-between rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 text-left transition hover:border-violet-500/30 hover:bg-violet-500/5"
                      >
                        <span className="font-display font-semibold text-white group-hover:text-violet-200">
                          {h.ticker}
                        </span>
                        <span className="flex items-center gap-2 text-xs">
                          {h.shares != null && (
                            <span className="text-slate-500">
                              {h.shares.toLocaleString()} sh
                              {h.unrealized_gain_pct != null && (
                                <span
                                  className={
                                    h.unrealized_gain_pct >= 0
                                      ? "ml-1 text-emerald-400"
                                      : "ml-1 text-red-400"
                                  }
                                >
                                  {h.unrealized_gain_pct >= 0 ? "+" : ""}
                                  {h.unrealized_gain_pct.toFixed(1)}%
                                </span>
                              )}
                            </span>
                          )}
                          {h.pe_ratio != null && (
                            <span className="text-slate-500">
                              P/E {h.pe_ratio.toFixed(0)}
                            </span>
                          )}
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${s.text} bg-white/[0.03]`}
                          >
                            {s.label}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <p className="py-4 text-center text-sm leading-relaxed text-slate-500">
              add tickers to your watchlist
              <br />
              <span className="text-slate-600">then peep your basket here</span>
            </p>
          )}

          <p className="mt-4 text-[10px] leading-relaxed text-slate-600">
            {data.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}
