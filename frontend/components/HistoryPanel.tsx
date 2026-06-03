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
  if (upper.includes("BUY")) return "text-emerald-400 bg-emerald-500/10 border-emerald-500/20";
  if (upper.includes("SELL")) return "text-red-400 bg-red-500/10 border-red-500/20";
  return "text-amber-400 bg-amber-500/10 border-amber-500/20";
}

export default function HistoryPanel({
  history,
  onSelect,
  loading,
}: HistoryPanelProps) {
  const items = history.slice(0, 10);

  return (
    <div className="card-surface card-surface-hover p-4">
      <h3 className="panel-title">recent runs</h3>
      <p className="mt-0.5 text-[11px] text-slate-600">tap to reload a report</p>

      <div className="mt-3 space-y-2">
        {items.length === 0 && !loading && (
          <p className="py-6 text-center text-sm text-slate-600">
            no runs yet — go analyze something
          </p>
        )}
        {loading && (
          <p className="text-sm text-slate-600">loading…</p>
        )}
        {items.map((entry, index) => (
          <button
            key={`${entry.ticker}-${entry.timestamp}-${index}`}
            type="button"
            onClick={() => onSelect(entry.ticker)}
            className="group w-full rounded-xl border border-white/[0.05] bg-white/[0.02] p-3 text-left transition hover:border-violet-500/25 hover:bg-violet-500/5"
          >
            <div className="flex items-center justify-between">
              <span className="font-display font-semibold text-white group-hover:text-violet-200">
                ${entry.ticker}
              </span>
              <RiskBadge level={entry.risk_level} size="sm" />
            </div>
            <p className="mt-1 text-[11px] text-slate-600">
              {formatTime(entry.timestamp)}
            </p>
            <p
              className={`mt-2 inline-block truncate max-w-full rounded-md border px-2 py-0.5 text-[10px] font-medium ${recBadge(entry.recommendation)}`}
            >
              {entry.recommendation.slice(0, 48)}
              {entry.recommendation.length > 48 ? "…" : ""}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
