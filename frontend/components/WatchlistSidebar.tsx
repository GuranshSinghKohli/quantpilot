"use client";

import { Plus, X } from "lucide-react";
import CompanyMark from "@/components/hud/CompanyMark";
import { ColumnHeader, NoSignal } from "@/components/hud/primitives";
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
    watchlist.some(
      (e) => e.ticker.toUpperCase() === currentTicker.toUpperCase()
    );

  return (
    <div>
      <ColumnHeader
        aside={watchlist.length > 0 ? `${watchlist.length} tracked` : undefined}
      >
        Watching
      </ColumnHeader>

      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs text-readout">
          Tickers QuantPilot keeps an eye on for you.
        </p>
        <button
          type="button"
          onClick={onAdd}
          disabled={!currentTicker || loading || Boolean(isOnList)}
          title={
            isOnList
              ? `${currentTicker} is already tracked`
              : (addDisabledReason ?? `Track ${currentTicker ?? "this ticker"}`)
          }
          aria-label={`Track ${currentTicker ?? "the current ticker"}`}
          className="bk bk-snap flex h-9 w-9 shrink-0 items-center justify-center text-osd transition-colors hover:text-caution disabled:opacity-40"
        >
          <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden />
        </button>
      </div>

      {watchlist.length === 0 ? (
        <NoSignal
          title="Nothing tracked yet"
          detail="Open a case, then add the ticker to keep it in view."
        />
      ) : (
        <ul className="space-y-1.5">
          {watchlist.map((entry) => {
            const active =
              currentTicker?.toUpperCase() === entry.ticker.toUpperCase();
            return (
              <li
                key={entry.ticker}
                className={`bk ${active ? "bk-active" : "bk-quiet"} flex items-center justify-between px-3 py-2`}
              >
                <button
                  type="button"
                  onClick={() => onSelect(entry.ticker)}
                  className={`font-display flex flex-1 items-center gap-2.5 text-left text-xs font-bold uppercase tracking-osd transition-colors hover:text-caution ${
                    active ? "text-caution" : "text-osd"
                  }`}
                >
                  <CompanyMark ticker={entry.ticker} size="sm" />
                  {entry.ticker}
                </button>
                <button
                  type="button"
                  onClick={() => onRemove(entry.ticker)}
                  className="ml-2 p-1 text-readout transition-colors hover:text-caution"
                  aria-label={`Stop tracking ${entry.ticker}`}
                >
                  <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
