"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchRecommendations } from "@/lib/api";
import type { RecommendationPick, RecommendationsResponse } from "@/types";

interface InvestmentIdeasPanelProps {
  onSelectTicker: (ticker: string) => void;
  isLoadingAnalysis?: boolean;
}

function outlookStyle(outlook: RecommendationPick["outlook"]) {
  if (outlook === "bullish") {
    return {
      badge: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
      bar: "bg-emerald-500",
    };
  }
  if (outlook === "bearish") {
    return {
      badge: "bg-red-500/15 text-red-400 border-red-500/30",
      bar: "bg-red-500",
    };
  }
  return {
    badge: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    bar: "bg-amber-500",
  };
}

export default function InvestmentIdeasPanel({
  onSelectTicker,
  isLoadingAnalysis = false,
}: InvestmentIdeasPanelProps) {
  const [data, setData] = useState<RecommendationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchRecommendations();
      setData(result);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load investment ideas"
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="card-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
            Near-term ideas
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            AI screens price, valuation &amp; news — not a buy list
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading || isLoadingAnalysis}
          className="shrink-0 rounded-lg border border-line px-2 py-1 text-xs text-slate-400 transition hover:border-accent hover:text-accent disabled:opacity-50"
        >
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      {loading && (
        <p className="mt-4 text-sm text-slate-500">Scanning popular stocks…</p>
      )}

      {error && !loading && (
        <p className="mt-4 text-sm text-red-400">{error}</p>
      )}

      {data && !loading && (
        <>
          <p className="mt-3 text-[10px] leading-relaxed text-slate-600">
            {data.horizon} · {data.scanned_tickers.length} tickers scanned
          </p>

          <ul className="mt-3 space-y-3">
            {data.picks.length === 0 && (
              <li className="text-sm text-slate-500">No picks available right now.</li>
            )}
            {data.picks.map((pick, index) => {
              const style = outlookStyle(pick.outlook);
              const pct = Math.round(pick.score * 100);
              return (
                <li
                  key={pick.ticker}
                  className="rounded-lg border border-line bg-[#0a0a0f]/50 p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => onSelectTicker(pick.ticker)}
                      disabled={isLoadingAnalysis}
                      className="text-left font-semibold text-white hover:text-accent disabled:opacity-50"
                    >
                      #{index + 1} {pick.ticker}
                    </button>
                    <span
                      className={`rounded border px-2 py-0.5 text-[10px] font-medium uppercase ${style.badge}`}
                    >
                      {pick.outlook}
                    </span>
                  </div>

                  {pick.current_price != null && (
                    <p className="mt-1 text-xs text-slate-500">
                      ${pick.current_price.toFixed(2)}
                    </p>
                  )}

                  <div className="mt-2">
                    <div className="flex justify-between text-[10px] text-slate-500">
                      <span>Setup score</span>
                      <span>{pct}%</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
                      <div
                        className={`h-full rounded-full ${style.bar}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>

                  <p className="mt-2 text-xs leading-relaxed text-slate-300">
                    {pick.near_term_view}
                  </p>

                  <ul className="mt-2 space-y-0.5">
                    {pick.reasons.slice(0, 2).map((reason, i) => (
                      <li key={i} className="text-[11px] text-slate-500">
                        • {reason}
                      </li>
                    ))}
                  </ul>
                </li>
              );
            })}
          </ul>

          <p className="mt-3 text-[10px] leading-relaxed text-slate-600">
            {data.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}
