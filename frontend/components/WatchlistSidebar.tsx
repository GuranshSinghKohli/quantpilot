"use client";

import type { WatchlistEntry } from "@/types";

interface WatchlistSidebarProps {
  watchlist: WatchlistEntry[];
  currentTicker: string | null;
  onSelect: (ticker: string) => void;
  onAdd: () => void;
  onRemove: (ticker: string) => void;
  loading?: boolean;
  addDisabledReason?: string | null;
}

export default function WatchlistSidebar({
  watchlist,
  currentTicker,
  onSelect,
  onAdd,
  onRemove,
  loading,
  addDisabledReason,
}: WatchlistSidebarProps) {
  const isOnList =
    currentTicker &&
    watchlist.some((e) => e.ticker.toUpperCase() === currentTicker.toUpperCase());

  return (
    <div className="card-surface p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
          Watchlist
        </h3>
        <button
          type="button"
          onClick={onAdd}
          disabled={!currentTicker || loading}
          title={
            isOnList
              ? `${currentTicker} is on your watchlist — click to confirm`
              : addDisabledReason ?? "Add current ticker to watchlist"
          }
          className="flex h-7 w-7 items-center justify-center rounded-lg border border-accent/50 text-accent transition hover:bg-accent/10 disabled:opacity-40"
        >
          +
        </button>
      </div>
      {!currentTicker && (
        <p className="mt-2 text-xs text-slate-600">
          Run an analysis first, then add the ticker here.
        </p>
      )}

      <div className="mt-3 space-y-1">
        {watchlist.length === 0 && (
          <p className="py-6 text-center text-sm leading-relaxed text-slate-500">
            No tickers saved yet.
            <br />
            <span className="text-slate-600">Run an analysis, then tap + to track it here.</span>
          </p>
        )}
        {watchlist.map((entry) => (
          <div
            key={entry.ticker}
            className={`flex items-center justify-between rounded-lg px-3 py-2 transition ${
              currentTicker === entry.ticker
                ? "bg-accent/10 border border-accent/30"
                : "hover:bg-[#1e1e2e]/50"
            }`}
          >
            <button
              type="button"
              onClick={() => onSelect(entry.ticker)}
              className="flex-1 text-left text-sm font-medium text-slate-200 hover:text-accent"
            >
              {entry.ticker}
            </button>
            <button
              type="button"
              onClick={() => onRemove(entry.ticker)}
              className="ml-2 text-slate-500 hover:text-red-400"
              aria-label={`Remove ${entry.ticker}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
