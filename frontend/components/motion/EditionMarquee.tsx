"use client";

import { cn } from "@/lib/utils";

const FALLBACK = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"];

export default function EditionMarquee({
  tickers = [],
  onPick,
}: {
  tickers?: string[];
  onPick?: (ticker: string) => void;
}) {
  const unique = Array.from(
    new Set(
      [...tickers, ...FALLBACK]
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean)
    )
  ).slice(0, 12);
  const loop = [...unique, ...unique];

  return (
    <div
      className="overflow-hidden border-y border-hud bg-panel"
      aria-label="Recent tickers"
    >
      <div className="flex w-max animate-tape-run">
        {loop.map((ticker, i) => (
          <button
            key={`${ticker}-${i}`}
            type="button"
            onClick={() => onPick?.(ticker)}
            className={cn(
              "shrink-0 border-r border-hud px-5 py-2 font-mono text-sm uppercase tracking-osd text-cyan",
              "hover:bg-caution hover:text-letterbox hover:shadow-neon"
            )}
          >
            {ticker}
          </button>
        ))}
      </div>
    </div>
  );
}
