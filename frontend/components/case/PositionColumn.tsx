"use client";

import { useCallback, useEffect, useState } from "react";
import CompanyMark from "@/components/hud/CompanyMark";
import {
  Bracketed,
  Caution,
  ColumnHeader,
  HudButton,
  NoSignal,
} from "@/components/hud/primitives";
import { ApiError, fetchPortfolioAnalysis, sweepInvestigations } from "@/lib/api";
import type { AuthUser, PortfolioAnalysis, WatchlistEntry } from "@/types";

type Props = {
  user: AuthUser | null;
  watchlist: WatchlistEntry[];
  currentTicker: string | null;
  isOnWatchlist: boolean;
  watchlistLoading: boolean;
  onAddWatchlist: () => void;
  onOpenTicker: (ticker: string) => void;
};

function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function pct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "\u2212" : "+";
  return `${sign}${Math.abs(value).toFixed(2)}`;
}

export default function PositionColumn({
  user,
  watchlist,
  currentTicker,
  isOnWatchlist,
  watchlistLoading,
  onAddWatchlist,
  onOpenTicker,
}: Props) {
  const [analysis, setAnalysis] = useState<PortfolioAnalysis | null>(null);
  const [scanning, setScanning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) {
      setAnalysis(null);
      return;
    }
    let cancelled = false;
    void fetchPortfolioAnalysis()
      .then((next) => {
        if (!cancelled) setAnalysis(next);
      })
      .catch(() => {
        /* holdings are optional until the user adds some */
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

  const scanHoldings = useCallback(async () => {
    setScanning(true);
    setError(null);
    setNotice(null);
    try {
      const result = await sweepInvestigations({ dry_run: false });
      setNotice(
        result.launched > 0
          ? `Opened ${result.launched} ${
              result.launched === 1 ? "case" : "cases"
            } across ${result.evaluated} holdings.`
          : `Checked ${result.evaluated} holdings. Nothing moved enough to open a case.`
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not scan holdings. Try again in a moment."
      );
    } finally {
      setScanning(false);
    }
  }, []);

  const holdings = analysis?.holdings ?? [];

  return (
    <Bracketed
      as="section"
      large
      id="your-position"
      aria-label="Your position"
      className="flex h-full flex-col p-3 sm:p-4"
    >
      <ColumnHeader
        aside={
          analysis?.total_market_value != null
            ? `$${money(analysis.total_market_value)}`
            : undefined
        }
        brief="What this move did to money you actually hold, once your holdings are connected."
      >
        Your position
      </ColumnHeader>

      {!user ? (
        <NoSignal
          compact
          title="Not signed in"
          detail="Sign in and your holdings sit beside the case, so you can see what the move did to money you actually hold."
          action={
            <p className="osd-label">Sign in from the bar above</p>
          }
        />
      ) : holdings.length > 0 ? (
        <div className="-mx-1 overflow-x-auto">
          <table className="w-full min-w-[19rem] border-collapse text-left">
            <caption className="sr-only">
              Your holdings with market value and unrealized change
            </caption>
            <thead>
              <tr className="border-b border-hud">
                <th scope="col" className="osd-label pb-2 pl-1 font-medium">
                  Ticker
                </th>
                <th
                  scope="col"
                  className="osd-label pb-2 text-right font-medium"
                >
                  Shares
                </th>
                <th
                  scope="col"
                  className="osd-label pb-2 text-right font-medium"
                >
                  Value
                </th>
                <th
                  scope="col"
                  className="osd-label pb-2 pr-1 text-right font-medium"
                >
                  Chg %
                </th>
              </tr>
            </thead>
            <tbody>
              {holdings.slice(0, 8).map((h) => {
                const down =
                  h.unrealized_gain_pct != null && h.unrealized_gain_pct < 0;
                return (
                  <tr
                    key={h.ticker}
                    className="border-b border-hud/60 last:border-0"
                  >
                    <th scope="row" className="py-2 pl-1 font-normal">
                      <button
                        type="button"
                        onClick={() => onOpenTicker(h.ticker)}
                        className="font-display flex items-center gap-2 text-xs font-bold uppercase tracking-osd text-osd transition-colors hover:text-caution"
                      >
                        <CompanyMark ticker={h.ticker} size="sm" />
                        {h.ticker}
                      </button>
                    </th>
                    <td className="osd-readout py-2 text-right text-readout">
                      {h.shares ?? "—"}
                    </td>
                    <td className="osd-readout py-2 text-right">
                      {money(h.market_value)}
                    </td>
                    <td
                      className={`osd-readout py-2 pr-1 text-right ${
                        down ? "text-down" : "text-up"
                      }`}
                    >
                      {pct(h.unrealized_gain_pct)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : watchlist.length > 0 ? (
        <div>
          <p className="osd-label mb-3">Watching</p>
          <ul className="flex flex-wrap gap-2">
            {watchlist.slice(0, 8).map((entry) => (
              <li key={entry.ticker}>
                <button
                  type="button"
                  onClick={() => onOpenTicker(entry.ticker)}
                  className="bk bk-quiet bk-snap flex min-h-9 items-center gap-2 px-2.5 py-1.5 font-display text-xs font-bold uppercase tracking-osd text-readout transition-colors hover:text-osd"
                >
                  <CompanyMark ticker={entry.ticker} size="sm" />
                  {entry.ticker}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <NoSignal
          title="No holdings yet"
          detail="Add a ticker you own and QuantPilot will watch it for moves worth explaining."
        />
      )}

      {analysis?.summary && (
        <p className="mt-4 border-t border-hud pt-4 text-xs leading-relaxed text-readout">
          {analysis.summary}
        </p>
      )}

      {/* Actions belong to a signed-in account; offering them to a visitor is
          an invitation to a dead end. */}
      <div className={`space-y-2 pt-6 ${user ? "" : "hidden"}`}>
        {notice && (
          <p role="status" className="text-xs leading-relaxed text-osd/85">
            {notice}
          </p>
        )}
        {error && <Caution title="Scan failed">{error}</Caution>}

        <HudButton
          variant="primary"
          onClick={scanHoldings}
          loading={scanning}
          disabled={!user}
          className="w-full"
          title={
            user
              ? "Check every holding for a move worth investigating"
              : "Sign in to scan your holdings"
          }
        >
          {scanning ? "Scanning holdings…" : "Scan my holdings"}
        </HudButton>

        <HudButton
          variant="secondary"
          onClick={onAddWatchlist}
          loading={watchlistLoading}
          disabled={!currentTicker || isOnWatchlist}
          className="w-full"
          title={
            !currentTicker
              ? "Frame a ticker first"
              : isOnWatchlist
                ? `${currentTicker} is already tracked`
                : `Track ${currentTicker}`
          }
        >
          {isOnWatchlist && currentTicker
            ? `${currentTicker} tracked`
            : currentTicker
              ? `Add ${currentTicker} to watchlist`
              : "Add to watchlist"}
        </HudButton>
      </div>
    </Bracketed>
  );
}
