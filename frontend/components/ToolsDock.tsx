"use client";

import { useState } from "react";
import HistoryPanel from "@/components/HistoryPanel";
import InvestmentIdeasPanel from "@/components/InvestmentIdeasPanel";
import PortfolioPanel from "@/components/PortfolioPanel";
import WatchlistSidebar from "@/components/WatchlistSidebar";
import type { HistoryEntry, WatchlistEntry } from "@/types";

export type ToolsTab = "watchlist" | "portfolio" | "ideas" | "history";

const TABS: { id: ToolsTab; label: string; icon: string }[] = [
  { id: "watchlist", label: "Watchlist", icon: "★" },
  { id: "portfolio", label: "Portfolio", icon: "◈" },
  { id: "ideas", label: "Hot picks", icon: "↑" },
  { id: "history", label: "History", icon: "↺" },
];

interface ToolsDockProps {
  watchlist: WatchlistEntry[];
  history: HistoryEntry[];
  currentTicker: string | null;
  watchlistLoading: boolean;
  historyLoading: boolean;
  isLoadingAnalysis: boolean;
  onSelectTicker: (ticker: string) => void;
  onHistorySelect: (ticker: string) => void;
  onAddWatchlist: () => void;
  onRemoveWatchlist: (ticker: string) => void;
}

export default function ToolsDock({
  watchlist,
  history,
  currentTicker,
  watchlistLoading,
  historyLoading,
  isLoadingAnalysis,
  onSelectTicker,
  onHistorySelect,
  onAddWatchlist,
  onRemoveWatchlist,
}: ToolsDockProps) {
  const [active, setActive] = useState<ToolsTab | null>(null);

  function toggle(tab: ToolsTab) {
    setActive((prev) => (prev === tab ? null : tab));
  }

  return (
    <section className="mt-6 sm:mt-8" aria-label="Tools">
      <div className="flex flex-wrap gap-2">
        {TABS.map((tab) => {
          const isActive = active === tab.id;
          const badge =
            tab.id === "watchlist" && watchlist.length > 0
              ? watchlist.length
              : tab.id === "history" && history.length > 0
                ? history.length
                : null;

          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => toggle(tab.id)}
              aria-expanded={isActive}
              className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition ${
                isActive
                  ? "border-violet-500/50 bg-violet-500/15 text-violet-200 shadow-glow"
                  : "border-white/[0.08] bg-white/[0.03] text-slate-400 hover:border-violet-500/30 hover:bg-violet-500/5 hover:text-slate-200"
              }`}
            >
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-lg text-xs ${
                  isActive ? "bg-violet-500/25 text-violet-300" : "bg-white/[0.05] text-slate-500"
                }`}
                aria-hidden
              >
                {tab.icon}
              </span>
              <span className="font-display">{tab.label}</span>
              {badge != null && (
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                    isActive
                      ? "bg-violet-500/30 text-violet-200"
                      : "bg-white/[0.06] text-slate-500"
                  }`}
                >
                  {badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {active && (
        <div className="mt-4">
          {active === "watchlist" && (
            <WatchlistSidebar
              watchlist={watchlist}
              currentTicker={currentTicker}
              onSelect={onSelectTicker}
              onAdd={onAddWatchlist}
              onRemove={onRemoveWatchlist}
              loading={watchlistLoading}
              addDisabledReason={
                !currentTicker ? "Run an analysis first" : null
              }
            />
          )}
          {active === "portfolio" && (
            <PortfolioPanel onSelectTicker={onSelectTicker} />
          )}
          {active === "ideas" && (
            <InvestmentIdeasPanel
              onSelectTicker={onSelectTicker}
              isLoadingAnalysis={isLoadingAnalysis}
            />
          )}
          {active === "history" && (
            <HistoryPanel
              history={history}
              onSelect={onHistorySelect}
              loading={historyLoading}
            />
          )}
        </div>
      )}
    </section>
  );
}
