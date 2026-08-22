"use client";

import type { ReactNode } from "react";
import CompanyMark from "@/components/hud/CompanyMark";
import MoveFigure from "@/components/hud/MoveFigure";
import QuoteStats from "@/components/hud/QuoteStats";
import FieldBackground from "@/components/motion/FieldBackground";
import { cn } from "@/lib/utils";
import { Stamp } from "@/components/hud/primitives";
import type { StockData } from "@/types";

export type MoveHeadlineProps = {
  ticker: string | null;
  movePct?: number | null;
  windowLabel?: string;
  triggerReason?: string | null;
  status?: string | null;
  busy?: boolean;
  windowControl?: ReactNode;
  flagged?: boolean;
  flagNote?: string | null;
  note?: string | null;
  isExample?: boolean;
  quote?: StockData | null;
};

const WINDOW_WORDS: Record<string, string> = {
  "1d": "1 day",
  "1w": "1 week",
  "1mo": "1 month",
  "6mo": "6 months",
  "1y": "1 year",
};

const WINDOW_PERIODS: Record<string, string> = {
  "1d": "today",
  "1w": "past week",
  "1mo": "past month",
  "6mo": "past 6 months",
  "1y": "past year",
};

const TRIGGER_WORDS: Record<string, string> = {
  on_demand: "requested",
  idiosyncratic: "idiosyncratic",
  vol_adjusted: "vol-adjusted",
  scheduled: "scheduled sweep",
  holdings_sweep: "holdings sweep",
};

function formatMove(pct: number): string {
  const sign = pct < 0 ? "\u2212" : "+";
  return `${sign}${Math.abs(pct).toFixed(2)}%`;
}

function Corner({ className }: { className: string }) {
  return (
    <span
      className={cn("pointer-events-none absolute h-3 w-3 border-caution", className)}
      aria-hidden
    />
  );
}

export default function MoveHeadline({
  ticker,
  movePct,
  windowLabel = "1d",
  triggerReason,
  busy = false,
  windowControl,
  flagged = false,
  flagNote,
  note,
  isExample = false,
  quote = null,
}: MoveHeadlineProps) {
  const hasMove = typeof movePct === "number" && Number.isFinite(movePct);
  const window = WINDOW_WORDS[windowLabel] ?? windowLabel;
  const period = WINDOW_PERIODS[windowLabel] ?? window;
  const trigger = triggerReason
    ? (TRIGGER_WORDS[triggerReason] ?? triggerReason.replace(/_/g, " "))
    : null;

  const headline = ticker
    ? hasMove
      ? `${ticker} ${formatMove(movePct as number)}`
      : ticker
    : "Search a ticker";

  const direction = hasMove
    ? (movePct as number) < 0
      ? "fell"
      : "rose"
    : null;
  const brief = !ticker
    ? "Name a ticker above and QuantPilot investigates why it moved, then shows you the evidence behind every explanation."
    : busy
      ? `Working out why ${ticker} moved over ${window}. Explanations appear below as the evidence lands.`
      : direction
        ? `Why ${ticker} ${direction} over ${window} — each explanation below is ranked by how much evidence backs it.${
            isExample
              ? " This is a saved example; search a ticker to run your own."
              : ""
          }`
        : `The case for ${ticker} is open. Explanations and receipts appear below.`;

  return (
    <section
      id="move-under-investigation"
      aria-label="Move under investigation"
      className="px-3 pt-3 sm:px-4"
      data-enter
    >
      <div className="relative overflow-hidden border border-caution/30 bg-hud-deep/30 shadow-neon cyber-chamfer">
        <FieldBackground busy={busy} />
        <Corner className="left-2 top-2 border-l border-t" />
        <Corner className="right-2 top-2 border-r border-t" />
        <Corner className="bottom-2 left-2 border-b border-l" />
        <Corner className="bottom-2 right-2 border-b border-r" />

        {ticker && (
          <CompanyMark
            ticker={ticker}
            size="watermark"
            className="pointer-events-none absolute -right-4 top-1/2 -translate-y-1/2 opacity-10 sm:right-8"
          />
        )}

        <div className="relative z-10 flex flex-col gap-4 p-4 lg:flex-row lg:items-start lg:justify-between lg:gap-8 lg:p-5">
          <div className="min-w-0 lg:w-[42%]">
            <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="osd-label text-cyan">
                {window}
                {trigger ? ` · ${trigger}` : ""}
              </span>
              {isExample && <Stamp tone="osd">Example</Stamp>}
            </div>

            <h2
              data-figures
              className={cn(
                "osd-figure min-w-0 text-[clamp(1.75rem,5vw,3.25rem)]",
                hasMove && (movePct as number) < 0
                  ? "text-down"
                  : "cyber-glitch text-caution",
                busy && "opacity-40"
              )}
            >
              {ticker && hasMove ? (
                <MoveFigure
                  key={`${ticker}-${windowLabel}`}
                  ticker={ticker}
                  movePct={movePct as number}
                />
              ) : (
                <span>{headline}</span>
              )}
            </h2>

            {hasMove && (
              <p className="mt-2 font-mono text-sm uppercase tracking-osd text-readout">
                {period}
              </p>
            )}

            <p className="mt-4 max-w-2xl text-sm leading-relaxed tracking-wide text-osd sm:text-base">
              {brief}
            </p>
          </div>

          <div className="min-w-0 border-t border-hud pt-3 lg:w-[32%] lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            <QuoteStats quote={quote} />
          </div>

          <div className="shrink-0 border-t border-hud pt-3 lg:w-[22%] lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            {windowControl}
          </div>
        </div>

        {flagged && (
          <p className="relative z-10 px-4 pb-4 text-sm text-down sm:px-5">
            {flagNote ?? "This case carries unverified material."}
          </p>
        )}
        {!flagged && note && (
          <p className="relative z-10 px-4 pb-4 text-sm text-magenta sm:px-5">
            {note}
          </p>
        )}
      </div>
    </section>
  );
}
