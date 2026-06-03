"use client";

import { useCallback, useEffect, useState } from "react";
import type { PortfolioAnalysis } from "@/types";
import { ApiError, fetchPortfolioAnalysis } from "@/lib/api";

interface PortfolioPanelProps {
  onSelectTicker?: (ticker: string) => void;
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

export default function PortfolioPanel({ onSelectTicker }: PortfolioPanelProps) {
  const [data, setData] = useState<PortfolioAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="card-surface card-surface-hover p-5">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <h3 className="panel-title">portfolio basket</h3>
          <p className="mt-0.5 text-[11px] text-slate-600">your watchlist, aggregated</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-lg border border-white/[0.08] px-2.5 py-1 text-xs text-slate-400 transition hover:border-violet-500/30 hover:text-violet-300 disabled:opacity-50"
        >
          {loading ? "…" : "↻"}
        </button>
      </div>

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
                    avg P/E
                  </p>
                  <p className="font-display mt-1 text-2xl font-bold text-white">
                    {data.avg_pe ?? "—"}
                  </p>
                </div>
              </div>

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
