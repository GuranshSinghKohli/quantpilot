"use client";

import type { ReactNode } from "react";
import AuthPanel from "@/components/AuthPanel";
import { HudButton, Readout } from "@/components/hud/primitives";
import type { AuthUser } from "@/types";

interface AppHeaderProps {
  apiReachable: boolean | null;
  user: AuthUser | null;
  onUserChange: (user: AuthUser | null) => void;
  sourcesAvailable?: boolean;
  onSourcesClick?: () => void;
  onPortfolioClick?: () => void;
  search?: ReactNode;
  caseId?: number | null;
}

export default function AppHeader({
  apiReachable,
  user,
  onUserChange,
  sourcesAvailable = false,
  onSourcesClick,
  onPortfolioClick,
  search,
  caseId,
}: AppHeaderProps) {
  const folio = caseId != null ? String(caseId).padStart(6, "0") : "000000";
  const link = apiReachable === false ? "DOWN" : apiReachable ? "UP" : "…";

  return (
    <header className="relative z-40 border-b border-hud bg-letterbox">
      <div className="flex items-center justify-between gap-3 border-b border-hud px-3 py-1.5 font-mono text-[0.6875rem] uppercase tracking-osd text-readout sm:px-4">
        <p>
          <span className="text-caution">&gt;</span> graph // case {folio}
        </p>
        <p className="hidden text-cyan sm:block">
          {new Date().toISOString().slice(0, 10)}
        </p>
        <p>
          link{" "}
          <span className={apiReachable === false ? "text-down" : "text-caution"}>
            {link}
          </span>
          <span className="cyber-cursor">_</span>
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 sm:px-4">
        <h1 className="cyber-glitch shrink-0 font-display text-xl font-black uppercase tracking-wide text-osd sm:text-2xl">
          QuantPilot
        </h1>

        {search && (
          <div className="min-w-0 flex-1 basis-full sm:basis-0">{search}</div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {apiReachable === false && (
            <Readout label="Uplink down" className="border-down text-down" />
          )}

          {onPortfolioClick && (
            <HudButton
              variant="ghost"
              onClick={onPortfolioClick}
              title="Jump to your position"
              className="min-h-9 px-2 py-1.5"
            >
              Position
            </HudButton>
          )}

          <HudButton
            variant="ghost"
            onClick={onSourcesClick}
            disabled={!sourcesAvailable}
            title={
              sourcesAvailable
                ? "Jump to data sources and provenance"
                : "Run a research report to view sources"
            }
            className="hidden min-h-9 px-2 py-1.5 sm:inline-flex"
          >
            Sources
          </HudButton>

          <AuthPanel user={user} onUserChange={onUserChange} />
        </div>
      </div>
    </header>
  );
}
