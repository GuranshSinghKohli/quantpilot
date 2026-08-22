"use client";

import {
  Bell,
  Briefcase,
  Eye,
  History,
  Newspaper,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import AlertsPanel from "@/components/AlertsPanel";
import BriefingPanel from "@/components/BriefingPanel";
import HistoryPanel from "@/components/HistoryPanel";
import InvestmentIdeasPanel from "@/components/InvestmentIdeasPanel";
import PortfolioPanel from "@/components/PortfolioPanel";
import WatchlistSidebar from "@/components/WatchlistSidebar";
import { Bracketed } from "@/components/hud/primitives";
import { useLenis } from "lenis/react";
import { cn } from "@/lib/utils";
import type { AuthUser, HistoryEntry, WatchlistEntry } from "@/types";

export type ToolsTab =
  | "watchlist"
  | "portfolio"
  | "briefing"
  | "alerts"
  | "ideas"
  | "history";

const TABS: { id: ToolsTab; label: string; icon: LucideIcon }[] = [
  { id: "watchlist", label: "Watching", icon: Eye },
  { id: "portfolio", label: "Holdings", icon: Briefcase },
  { id: "briefing", label: "Briefing", icon: Newspaper },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "ideas", label: "Ideas", icon: TrendingUp },
  { id: "history", label: "Reports", icon: History },
];

interface ToolsDockProps {
  user: AuthUser | null;
  watchlist: WatchlistEntry[];
  history: HistoryEntry[];
  currentTicker: string | null;
  watchlistLoading: boolean;
  historyLoading: boolean;
  isLoadingAnalysis: boolean;
  onSelectTicker: (ticker: string) => void;
  onOpenInvestigation?: (investigationId: number, ticker?: string) => void;
  onHistorySelect: (ticker: string) => void;
  onAddWatchlist: () => void;
  onRemoveWatchlist: (ticker: string) => void;
  /** When this key changes, open the dock on `focusTab` (e.g. header Position). */
  focusRequest?: { tab: ToolsTab; key: number } | null;
}

export default function ToolsDock({
  user,
  watchlist,
  history,
  currentTicker,
  watchlistLoading,
  historyLoading,
  isLoadingAnalysis,
  onSelectTicker,
  onOpenInvestigation,
  onHistorySelect,
  onAddWatchlist,
  onRemoveWatchlist,
  focusRequest = null,
}: ToolsDockProps) {
  const [active, setActive] = useState<ToolsTab | null>(null);
  const [alertUnread, setAlertUnread] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const lenis = useLenis();

  useEffect(() => {
    if (!focusRequest) return;
    setActive(focusRequest.tab);
    const id = window.setTimeout(() => {
      if (lenis) lenis.scrollTo("#tools-dock", { offset: -24 });
      else
        rootRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
    return () => window.clearTimeout(id);
  }, [focusRequest, lenis]);

  return (
    <div ref={rootRef} id="tools-dock" className="scroll-mt-24">
      <div
        className="grid grid-cols-3 gap-2 lg:grid-cols-6"
        role="tablist"
        aria-label="Tools"
      >
        {TABS.map((tab) => {
          const isActive = active === tab.id;
          const Icon = tab.icon;
          const badge =
            tab.id === "watchlist" && watchlist.length > 0
              ? watchlist.length
              : tab.id === "history" && history.length > 0
                ? history.length
                : tab.id === "alerts" && alertUnread > 0
                  ? alertUnread
                  : null;

          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setActive(isActive ? null : tab.id)}
              className={cn(
                "bk bk-snap relative flex min-h-[4.5rem] flex-col items-center justify-center gap-2 px-2 py-3",
                "text-xs font-semibold transition-colors",
                isActive
                  ? "bk-active text-caution"
                  : "bk-quiet text-readout hover:text-osd"
              )}
            >
              <Icon className="h-5 w-5" strokeWidth={1.5} aria-hidden />
              {tab.label}
              {badge != null && (
                <span
                  data-figures
                  className="osd-readout absolute right-2 top-2 text-[0.625rem] text-caution"
                >
                  {badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {active ? (
        <Bracketed
          large
          className="mt-3 p-3 sm:p-4"
          id={active === "portfolio" ? "portfolio-analysis" : undefined}
          role="tabpanel"
        >
          {active === "watchlist" && (
            <WatchlistSidebar
              watchlist={watchlist}
              currentTicker={currentTicker}
              onSelect={onSelectTicker}
              onAdd={onAddWatchlist}
              onRemove={onRemoveWatchlist}
              loading={watchlistLoading}
              addDisabledReason={
                !currentTicker ? "Frame a ticker first" : null
              }
            />
          )}
          {active === "portfolio" && (
            <PortfolioPanel onSelectTicker={onSelectTicker} user={user} />
          )}
          {active === "briefing" && (
            <BriefingPanel user={user} onSelectTicker={onSelectTicker} />
          )}
          {active === "alerts" && (
            <AlertsPanel
              user={user}
              onSelectTicker={onSelectTicker}
              onOpenInvestigation={onOpenInvestigation}
              onUnreadChange={setAlertUnread}
            />
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
        </Bracketed>
      ) : null}
    </div>
  );
}
