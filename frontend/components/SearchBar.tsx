"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

interface SearchBarProps {
  onInvestigate: (ticker: string) => void;
  onDeepResearch?: (ticker: string) => void;
  isLoading: boolean;
  recentTickers: string[];
  loadingLabel?: string;
  disabled?: boolean;
}

const QUICK_PICKS = ["AAPL", "MSFT", "NVDA", "TSLA"];

export default function SearchBar({
  onInvestigate,
  onDeepResearch,
  isLoading,
  recentTickers,
  loadingLabel = "Investigating…",
  disabled = false,
}: SearchBarProps) {
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const locked = isLoading || disabled;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  function validate(symbol: string): string | null {
    const clean = symbol.trim().toUpperCase();
    if (!clean) return "Enter a ticker symbol.";
    if (clean.length > 5) return "Ticker must be 5 characters or fewer.";
    if (!/^[A-Z0-9]+$/.test(clean)) return "Use letters and numbers only.";
    return null;
  }

  function submitInvestigate(value: string) {
    if (locked) return;
    const validationError = validate(value);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    const symbol = value.trim().toUpperCase();
    setTicker(symbol);
    onInvestigate(symbol);
  }

  function submitDeepResearch() {
    if (locked || !onDeepResearch) return;
    const validationError = validate(ticker);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    onDeepResearch(ticker.trim().toUpperCase());
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submitInvestigate(ticker);
  }

  const chips = recentTickers.length > 0 ? recentTickers : QUICK_PICKS;

  return (
    <div className="w-full">
      <label htmlFor="ticker-search" className="sr-only">
        Stock ticker to investigate
      </label>
      <form onSubmit={handleSubmit} className="relative flex flex-col gap-3">
        <div className="relative">
          <input
            id="ticker-search"
            ref={inputRef}
            type="text"
            value={ticker}
            onChange={(e) => {
              setTicker(e.target.value.toUpperCase());
              setError(null);
            }}
            placeholder="Why did NVDA move? Drop a ticker…"
            disabled={locked}
            autoComplete="off"
            className={`w-full rounded-2xl border border-violet-500/25 bg-[#0f0f18]/90 px-5 py-4 text-base text-white outline-none ring-violet-500/20 transition placeholder:text-slate-600 focus:border-violet-400/50 focus:ring-2 disabled:opacity-50 sm:text-lg ${
              isLoading ? "sm:pr-48" : "sm:pr-44"
            }`}
          />
          {!isLoading && (
            <span className="pointer-events-none absolute right-[8.5rem] top-1/2 hidden -translate-y-1/2 rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-600 sm:inline">
              ⌘K
            </span>
          )}
          <button
            type="submit"
            disabled={locked}
            aria-busy={isLoading}
            className={`absolute right-2 top-1/2 z-10 flex max-w-[12rem] -translate-y-1/2 items-center justify-center gap-2 truncate rounded-xl px-4 py-2.5 text-sm font-semibold text-white shadow-none outline-none ring-0 transition disabled:cursor-not-allowed disabled:opacity-70 ${
              isLoading
                ? "bg-violet-600/90"
                : "btn-vibe hover:brightness-110"
            }`}
          >
            {isLoading ? (
              <>
                <span
                  className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-white/30 border-t-white"
                  aria-hidden
                />
                <span className="truncate">{loadingLabel}</span>
              </>
            ) : (
              "investigate →"
            )}
          </button>
        </div>
      </form>

      {onDeepResearch && (
        <p className="mt-2 text-xs text-slate-600">
          Opens the Evidence Ledger.{" "}
          <button
            type="button"
            onClick={submitDeepResearch}
            disabled={locked}
            className="text-slate-400 underline decoration-white/15 underline-offset-2 transition hover:text-violet-300 disabled:opacity-50"
          >
            Or run a full research report
          </button>
        </p>
      )}

      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="w-full text-xs text-slate-600 sm:w-auto">
          {recentTickers.length > 0 ? "recent" : "try"}
        </span>
        {chips.map((sym) => (
          <button
            key={sym}
            type="button"
            onClick={() => submitInvestigate(sym)}
            disabled={locked}
            className="min-h-[36px] rounded-full border border-white/[0.08] bg-white/[0.03] px-4 py-1.5 text-sm font-medium text-slate-300 transition hover:border-violet-500/40 hover:bg-violet-500/10 hover:text-violet-200 disabled:opacity-50"
          >
            ${sym}
          </button>
        ))}
      </div>
    </div>
  );
}
