"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

export type InvestigateWindow = "1d" | "1w" | "1mo" | "1y";

interface SearchBarProps {
  onInvestigate: (ticker: string, windowLabel: InvestigateWindow) => void;
  onDeepResearch?: (ticker: string) => void;
  isLoading: boolean;
  recentTickers: string[];
  loadingLabel?: string;
  disabled?: boolean;
}

const QUICK_PICKS = ["AAPL", "MSFT", "NVDA", "TSLA"];

const WINDOWS: { value: InvestigateWindow; label: string; hint: string }[] = [
  { value: "1d", label: "1 day", hint: "Today’s move" },
  { value: "1w", label: "1 week", hint: "Last ~5 sessions" },
  { value: "1mo", label: "1 month", hint: "Past month" },
  { value: "1y", label: "1 year", hint: "Past year" },
];

export default function SearchBar({
  onInvestigate,
  onDeepResearch,
  isLoading,
  recentTickers,
  loadingLabel = "Investigating…",
  disabled = false,
}: SearchBarProps) {
  const [ticker, setTicker] = useState("");
  const [windowLabel, setWindowLabel] = useState<InvestigateWindow>("1d");
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
    onInvestigate(symbol, windowLabel);
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
      <div className="mb-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-300/80">
          Step 1 · Start here
        </p>
        <p className="mt-1 text-sm text-slate-500">
          Enter a ticker and pick how far back to look. This opens a new case in
          the Evidence Ledger below — it is not a search of past cases.
        </p>
      </div>

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
            placeholder="Ticker — e.g. TSLA"
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

        <fieldset disabled={locked} className="min-w-0">
          <legend className="sr-only">Investigation time window</legend>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-600">Look back</span>
            {WINDOWS.map((w) => {
              const active = windowLabel === w.value;
              return (
                <button
                  key={w.value}
                  type="button"
                  title={w.hint}
                  onClick={() => setWindowLabel(w.value)}
                  className={`rounded-full border px-3 py-1.5 text-xs font-medium transition disabled:opacity-50 ${
                    active
                      ? "border-violet-500/50 bg-violet-500/20 text-violet-100"
                      : "border-white/[0.08] bg-white/[0.03] text-slate-400 hover:border-violet-500/30 hover:text-slate-200"
                  }`}
                >
                  {w.label}
                </button>
              );
            })}
          </div>
        </fieldset>
      </form>

      {onDeepResearch && (
        <p className="mt-2 text-xs text-slate-600">
          Opens a new Evidence Ledger case for the selected window.{" "}
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
