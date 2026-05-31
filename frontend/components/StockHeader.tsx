"use client";

import type { AnalysisResponse, StockData } from "@/types";
import RiskBadge from "./RiskBadge";

interface StockHeaderProps {
  stock: StockData;
  analysis: AnalysisResponse | null;
  onAddToWatchlist: () => void;
  watchlistLoading?: boolean;
  isOnWatchlist?: boolean;
}

function formatCurrency(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatLargeNumber(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value);
}

export default function StockHeader({
  stock,
  analysis,
  onAddToWatchlist,
  watchlistLoading,
  isOnWatchlist = false,
}: StockHeaderProps) {
  const companyName =
    analysis?.final_report?.report_title?.split("—")[0]?.trim() ||
    analysis?.final_report?.report_title?.split("-")[0]?.trim() ||
    stock.ticker;

  const riskLevel = analysis?.risk_output?.risk_level;

  const stats = [
    { label: "Market Cap", value: formatLargeNumber(stock.market_cap) },
    { label: "P/E Ratio", value: stock.pe_ratio?.toFixed(2) ?? "—" },
    { label: "52W High", value: formatCurrency(stock.fifty_two_week_high) },
    { label: "52W Low", value: formatCurrency(stock.fifty_two_week_low) },
    {
      label: "Valuation",
      value: analysis?.metrics_output?.valuation_rating ?? "—",
    },
  ];

  return (
    <div className="card-surface p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-3xl font-bold tracking-tight text-white">
              {stock.ticker}
            </h2>
            {riskLevel && <RiskBadge level={riskLevel} />}
          </div>
          <p className="mt-1 text-slate-400">{companyName}</p>
          <p className="mt-3 text-4xl font-semibold text-white">
            {formatCurrency(stock.current_price)}
          </p>
        </div>
        <button
          type="button"
          onClick={onAddToWatchlist}
          disabled={watchlistLoading}
          className={`rounded-lg border px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${
            isOnWatchlist
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/15"
              : "border-accent/50 bg-accent/10 text-accent hover:bg-accent/20"
          }`}
        >
          {watchlistLoading
            ? "Adding…"
            : isOnWatchlist
              ? "✓ On Watchlist"
              : "+ Add to Watchlist"}
        </button>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="rounded-lg border border-[#1e1e2e] bg-[#0a0a0f]/50 px-3 py-2"
          >
            <p className="text-xs text-slate-500">{stat.label}</p>
            <p className="mt-0.5 text-sm font-semibold text-slate-200">
              {stat.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
