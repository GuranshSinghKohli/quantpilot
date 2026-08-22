"use client";

import { RefreshCw } from "lucide-react";
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
      badge: "bg-emerald-500/15 text-emerald-400 border-hud",
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
    <div className="card-surface card-surface-hover p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="panel-title">hot picks</h3>
          <p className="mt-0.5 text-[11px] text-readout">
            AI-scanned setups · not financial advice
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading || isLoadingAnalysis}
          aria-label="Reload ideas"
          className="bk bk-quiet bk-snap shrink-0 px-2.5 py-2 text-readout transition-colors hover:text-caution disabled:opacity-50"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
            strokeWidth={1.5}
            aria-hidden
          />
        </button>
      </div>

      {loading && (
        <div className="mt-4 space-y-2">
          <div className="skeleton-shimmer h-16 rounded-xl" />
          <div className="skeleton-shimmer h-16 rounded-xl" />
        </div>
      )}

      {error && !loading && (
        <p className="mt-4 text-sm text-red-400">{error}</p>
      )}

      {data && !loading && (
        <>
          <p className="mt-3 text-[10px] leading-relaxed text-readout">
            {data.horizon} · {data.scanned_tickers.length} tickers scanned
          </p>

          <ul className="mt-3 space-y-3">
            {data.picks.length === 0 && (
              <li className="text-sm text-readout">No picks available right now.</li>
            )}
            {data.picks.map((pick, index) => {
              const style = outlookStyle(pick.outlook);
              const pct = Math.round(pick.score * 100);
              return (
                <li
                  key={pick.ticker}
                  className="rounded-xl border border-hud bg-letterbox p-3 transition hover:border-osd"
                >
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => onSelectTicker(pick.ticker)}
                      disabled={isLoadingAnalysis}
                      className="text-left font-semibold text-osd hover:text-accent disabled:opacity-50"
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
                    <p className="mt-1 text-xs text-readout">
                      ${pick.current_price.toFixed(2)}
                    </p>
                  )}

                  <div className="mt-2">
                    <div className="flex justify-between text-[10px] text-readout">
                      <span>Setup score</span>
                      <span>{pct}%</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden bg-line">
                      <div
                        className={`h-full ${style.bar}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>

                  <p className="mt-2 text-xs leading-relaxed text-osd">
                    {pick.near_term_view}
                  </p>

                  {pick.catalyst_hint && (
                    <p className="mt-2 border border-cyan/40 bg-hud-deep/40 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-cyan">
                      Hint · {pick.catalyst_hint}
                    </p>
                  )}

                  <ul className="mt-2 space-y-0.5">
                    {pick.reasons.slice(0, 4).map((reason, i) => (
                      <li key={i} className="text-[11px] text-readout">
                        • {reason}
                      </li>
                    ))}
                  </ul>
                </li>
              );
            })}
          </ul>

          <p className="mt-3 text-[10px] leading-relaxed text-readout">
            {data.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}
