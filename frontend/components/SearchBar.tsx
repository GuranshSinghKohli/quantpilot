"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

interface SearchBarProps {
  onAnalyze: (ticker: string) => void;
  isLoading: boolean;
  recentTickers: string[];
}

const QUICK_PICKS = ["AAPL", "MSFT", "NVDA", "TSLA"];

export default function SearchBar({
  onAnalyze,
  isLoading,
  recentTickers,
}: SearchBarProps) {
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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

  function submit(value: string) {
    const validationError = validate(value);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    const symbol = value.trim().toUpperCase();
    setTicker(symbol);
    onAnalyze(symbol);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submit(ticker);
  }

  const chips = recentTickers.length > 0 ? recentTickers : QUICK_PICKS;

  return (
    <div className="w-full">
      <label htmlFor="ticker-search" className="sr-only">
        Stock ticker symbol
      </label>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:relative">
        <input
          id="ticker-search"
          ref={inputRef}
          type="text"
          value={ticker}
          onChange={(e) => {
            setTicker(e.target.value.toUpperCase());
            setError(null);
          }}
          placeholder="NVDA, AAPL, whatever you're curious about…"
          disabled={isLoading}
          autoComplete="off"
          className="w-full rounded-2xl border border-violet-500/25 bg-[#0f0f18]/90 px-5 py-4 text-base text-white outline-none ring-violet-500/20 transition placeholder:text-slate-600 focus:border-violet-400/50 focus:ring-2 disabled:opacity-50 sm:py-4 sm:pr-36 sm:text-lg"
        />
        <button
          type="submit"
          disabled={isLoading}
          className="btn-vibe w-full rounded-xl px-5 py-3.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:absolute sm:right-2 sm:top-1/2 sm:w-auto sm:-translate-y-1/2 sm:rounded-xl sm:px-5 sm:py-2.5"
        >
          {isLoading ? (
            <span className="inline-flex items-center justify-center gap-2">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              cooking…
            </span>
          ) : (
            "run it →"
          )}
        </button>
        <span className="hidden text-[10px] text-slate-600 sm:absolute sm:right-28 sm:top-1/2 sm:inline sm:-translate-y-1/2 sm:rounded-md sm:border sm:border-white/10 sm:bg-white/5 sm:px-2 sm:py-0.5">
          ⌘K
        </span>
      </form>

      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="w-full text-xs text-slate-600 sm:w-auto">
          {recentTickers.length > 0 ? "recent" : "trending"}
        </span>
        {chips.map((sym) => (
          <button
            key={sym}
            type="button"
            onClick={() => submit(sym)}
            disabled={isLoading}
            className="min-h-[36px] rounded-full border border-white/[0.08] bg-white/[0.03] px-4 py-1.5 text-sm font-medium text-slate-300 transition hover:border-violet-500/40 hover:bg-violet-500/10 hover:text-violet-200 disabled:opacity-50"
          >
            ${sym}
          </button>
        ))}
      </div>
    </div>
  );
}
