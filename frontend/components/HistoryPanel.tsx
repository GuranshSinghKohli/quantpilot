"use client";

import type { HistoryEntry } from "@/types";
import RiskBadge from "./RiskBadge";

interface HistoryPanelProps {
  history: HistoryEntry[];
  onSelect: (ticker: string) => void;
  loading?: boolean;
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

function recBadge(rec: string): string {
  const upper = rec.toUpperCase();
  if (upper.includes("BUY")) return "text-emerald-400";
  if (upper.includes("SELL")) return "text-red-400";
  return "text-amber-400";
}

export default function HistoryPanel({
  history,
  onSelect,
  loading,
}: HistoryPanelProps) {
  const items = history.slice(0, 10);

  return (
    <div className="card-surface p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
        Recent Analyses
      </h3>

      <div className="mt-3 space-y-2">
        {items.length === 0 && (
          <p className="py-4 text-center text-sm text-slate-500">
            No analyses yet this session
          </p>
        )}
        {loading && (
          <p className="text-sm text-slate-500">Loading history…</p>
        )}
        {items.map((entry, index) => (
          <button
            key={`${entry.ticker}-${entry.timestamp}-${index}`}
            type="button"
            onClick={() => onSelect(entry.ticker)}
            className="w-full rounded-lg border border-[#1e1e2e] bg-[#0a0a0f]/50 p-3 text-left transition hover:border-accent/40 hover:bg-accent/5"
          >
            <div className="flex items-center justify-between">
              <span className="font-semibold text-white">{entry.ticker}</span>
              <RiskBadge level={entry.risk_level} size="sm" />
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {formatTime(entry.timestamp)}
            </p>
            <p className={`mt-1 truncate text-xs font-medium ${recBadge(entry.recommendation)}`}>
              {entry.recommendation.slice(0, 60)}
              {entry.recommendation.length > 60 ? "…" : ""}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
