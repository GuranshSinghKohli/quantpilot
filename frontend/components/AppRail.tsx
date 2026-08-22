"use client";

import { useLenis } from "lenis/react";
import { cn } from "@/lib/utils";
import type { ToolsTab } from "@/components/ToolsDock";

const DEPARTMENTS = [
  { id: "investigate", label: "Investigate", href: "move-under-investigation" },
  { id: "watchlist", label: "Watching", tab: "watchlist" as ToolsTab },
  { id: "portfolio", label: "Holdings", tab: "portfolio" as ToolsTab },
  { id: "briefing", label: "Briefing", tab: "briefing" as ToolsTab },
  { id: "alerts", label: "Alerts", tab: "alerts" as ToolsTab },
  { id: "ideas", label: "Ideas", tab: "ideas" as ToolsTab },
  { id: "history", label: "Reports", tab: "history" as ToolsTab },
] as const;

export default function AppRail({
  onTool,
}: {
  onTool: (tab: ToolsTab) => void;
}) {
  const lenis = useLenis();

  function go(href?: string, tab?: ToolsTab) {
    if (tab) onTool(tab);
    const id = tab ? "tools-dock" : href;
    if (!id) return;
    if (lenis) lenis.scrollTo(`#${id}`, { offset: -24 });
    else document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <nav
      aria-label="Departments"
      className="z-30 border-b border-hud bg-letterbox"
    >
      <ul className="flex items-stretch overflow-x-auto px-2 sm:px-3">
        {DEPARTMENTS.map((item, i) => (
          <li key={item.id} className="flex">
            <button
              type="button"
              onClick={() =>
                go(
                  "href" in item ? item.href : undefined,
                  "tab" in item ? item.tab : undefined
                )
              }
              className={cn(
                "whitespace-nowrap px-3 py-2.5 font-mono text-[0.6875rem] uppercase tracking-osd text-readout",
                "hover:bg-caution/10 hover:text-caution hover:shadow-neon",
                i > 0 && "border-l border-hud"
              )}
            >
              {item.label}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
