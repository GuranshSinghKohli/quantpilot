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

export default function HistoryPanel({
  history,
  onSelect,
  loading,
}: HistoryPanelProps) {
  const items = history.slice(0, 10);

  return (
    <div className="card-surface card-surface-hover p-4">
      <h3 className="panel-title">recent runs</h3>
      <p className="mt-0.5 text-[11px] text-readout">tap to reload a report</p>

      <div className="mt-3 space-y-2">
        {items.length === 0 && !loading && (
          <p className="py-6 text-center text-sm text-readout">
            no runs yet. Go analyze something
          </p>
        )}
        {loading && (
          <p className="text-sm text-readout">loading…</p>
        )}
        {items.map((entry, index) => (
          <button
            key={`${entry.ticker}-${entry.timestamp}-${index}`}
            type="button"
            onClick={() => onSelect(entry.ticker)}
            className="group w-full rounded-xl border border-hud bg-letterbox p-3 text-left transition hover:border-osd hover:bg-caution-50"
          >
            <div className="flex items-center justify-between">
              <span className="font-display font-semibold text-osd group-hover:text-down">
                ${entry.ticker}
              </span>
              <RiskBadge level={entry.risk_level} size="sm" />
            </div>
            <p className="mt-1 text-[11px] text-readout">
              {formatTime(entry.timestamp)}
            </p>
            {entry.headline && (
              <p className="mt-2 truncate text-[11px] text-readout">{entry.headline}</p>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
