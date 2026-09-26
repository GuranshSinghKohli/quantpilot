"use client";

import { History, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import HistoryPanel from "@/components/HistoryPanel";
import { Bracketed } from "@/components/hud/primitives";
import { useLenis } from "lenis/react";
import { cn } from "@/lib/utils";
import type { HistoryEntry } from "@/types";

export type ToolsTab = "history";

const TABS: { id: ToolsTab; label: string; icon: LucideIcon }[] = [
  { id: "history", label: "Reports", icon: History },
];

interface ToolsDockProps {
  history: HistoryEntry[];
  historyLoading: boolean;
  onHistorySelect: (ticker: string) => void;
  focusRequest?: { tab: ToolsTab; key: number } | null;
}

export default function ToolsDock({
  history,
  historyLoading,
  onHistorySelect,
  focusRequest = null,
}: ToolsDockProps) {
  const [active, setActive] = useState<ToolsTab | null>(null);
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
      <div className="grid grid-cols-1 gap-2" role="tablist" aria-label="Tools">
        {TABS.map((tab) => {
          const isActive = active === tab.id;
          const Icon = tab.icon;
          const badge = history.length > 0 ? history.length : null;

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

      {active === "history" ? (
        <Bracketed large className="mt-3 p-3 sm:p-4" role="tabpanel">
          <HistoryPanel
            history={history}
            onSelect={onHistorySelect}
            loading={historyLoading}
          />
        </Bracketed>
      ) : null}
    </div>
  );
}
