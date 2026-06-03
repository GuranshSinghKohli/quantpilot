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
    <div className="card-surface card-surface-hover p-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="panel-title">watchlist</h3>
          <p className="mt-0.5 text-[11px] text-slate-600">stocks you&apos;re tracking</p>
        </div>
        <button
          type="button"
          onClick={onAdd}
          disabled={!currentTicker || loading}
          title={
            isOnList
              ? `${currentTicker} is already saved`
              : addDisabledReason ?? "Save current ticker"
          }
          className="flex h-8 w-8 items-center justify-center rounded-xl border border-violet-500/30 bg-violet-500/10 text-lg text-violet-300 transition hover:bg-violet-500/20 disabled:opacity-40"
        >
          +
        </button>
      </div>

      {!currentTicker && (
        <p className="mt-3 rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-xs text-slate-600">
          run an analysis first, then hit + to save it
        </p>
      )}

      <div className="mt-3 space-y-1">
        {watchlist.length === 0 && (
          <p className="py-8 text-center text-sm leading-relaxed text-slate-600">
            nothing saved yet
            <br />
            <span className="text-slate-700">your picks show up here</span>
          </p>
        )}
        {watchlist.map((entry) => {
          const active =
            currentTicker?.toUpperCase() === entry.ticker.toUpperCase();
          return (
            <div
              key={entry.ticker}
              className={`flex items-center justify-between rounded-xl px-3 py-2 transition ${
                active
                  ? "border border-violet-500/30 bg-violet-500/10"
                  : "border border-transparent hover:bg-white/[0.03]"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(entry.ticker)}
                className="font-display flex-1 text-left text-sm font-semibold text-slate-200 hover:text-violet-200"
              >
                ${entry.ticker}
              </button>
              <button
                type="button"
                onClick={() => onRemove(entry.ticker)}
                className="ml-2 rounded-md px-1.5 text-slate-600 transition hover:bg-red-500/10 hover:text-red-400"
                aria-label={`Remove ${entry.ticker}`}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
