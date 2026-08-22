"use client";

import { cn } from "@/lib/utils";
import type { StockData } from "@/types";

function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function compact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

function yieldPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const pct = value > 1 ? value : value * 100;
  return `${pct.toFixed(2)}%`;
}

function Cell({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0 border-b border-hud/70 py-1.5">
      <p className="osd-label">{label}</p>
      <p data-figures className="mt-0.5 font-mono text-sm text-osd">
        {value}
      </p>
    </div>
  );
}

export default function QuoteStats({
  quote,
  className,
}: {
  quote: StockData | null;
  className?: string;
}) {
  if (!quote) {
    return (
      <p className={cn("font-mono text-xs uppercase tracking-osd text-readout", className)}>
        Quote uplink pending
      </p>
    );
  }

  const low = quote.fifty_two_week_low;
  const high = quote.fifty_two_week_high;
  const last = quote.current_price;
  const span =
    low != null && high != null && high > low ? high - low : null;
  const pos =
    span != null && last != null
      ? Math.max(0, Math.min(1, (last - (low as number)) / span))
      : null;

  return (
    <div className={cn("min-w-0", className)}>
      {(quote.sector || quote.industry) && (
        <p className="osd-label mb-2 text-cyan">
          {[quote.sector, quote.industry].filter(Boolean).join(" · ")}
        </p>
      )}

      {pos != null && (
        <div className="mb-3">
          <div className="mb-1 flex justify-between font-mono text-[0.6875rem] uppercase tracking-osd text-readout">
            <span>52W {money(low)}</span>
            <span>{money(high)}</span>
          </div>
          <div
            className="relative h-1.5 bg-hud"
            role="img"
            aria-label={`Last is ${(pos * 100).toFixed(0)} percent through the 52-week range`}
          >
            <span
              className="absolute top-1/2 h-3 w-1.5 -translate-y-1/2 bg-caution shadow-neon"
              style={{ left: `calc(${pos * 100}% - 3px)` }}
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-4">
        <Cell label="Last" value={money(quote.current_price)} />
        <Cell label="Prev close" value={money(quote.previous_close)} />
        <Cell label="Day range" value={`${money(quote.day_low)} – ${money(quote.day_high)}`} />
        <Cell label="Mkt cap" value={compact(quote.market_cap)} />
        <Cell label="P/E" value={num(quote.pe_ratio)} />
        <Cell label="Fwd P/E" value={num(quote.forward_pe)} />
        <Cell label="EPS" value={money(quote.eps)} />
        <Cell label="Div yield" value={yieldPct(quote.dividend_yield)} />
        <Cell label="Beta" value={num(quote.beta)} />
        <Cell label="Volume" value={compact(quote.volume)} />
        <Cell label="Avg vol" value={compact(quote.avg_volume)} />
        <Cell
          label="52W range"
          value={`${money(quote.fifty_two_week_low)} – ${money(quote.fifty_two_week_high)}`}
        />
      </div>
    </div>
  );
}
