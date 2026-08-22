"use client";

import { FormEvent, useCallback, useId, useRef, useState } from "react";
import { useKeyPress } from "@/hooks/useKeyPress";
import { Bracketed, HudButton } from "@/components/hud/primitives";
import { cn } from "@/lib/utils";

export type InvestigateWindow = "1d" | "1w" | "1mo" | "6mo" | "1y";

interface SearchBarProps {
  onInvestigate: (ticker: string, windowLabel: InvestigateWindow) => void;
  onDeepResearch?: (ticker: string) => void;
  isLoading: boolean;
  loadingLabel?: string;
  disabled?: boolean;
  windowLabel?: InvestigateWindow;
}

export const QUICK_PICKS = ["AAPL", "MSFT", "NVDA", "TSLA"];

export const INVESTIGATE_WINDOWS: {
  value: InvestigateWindow;
  label: string;
  hint: string;
}[] = [
  { value: "1d", label: "1D", hint: "today's move" },
  { value: "1w", label: "1W", hint: "the last week" },
  { value: "1mo", label: "1M", hint: "the past month" },
  { value: "6mo", label: "6M", hint: "the past six months" },
  { value: "1y", label: "1Y", hint: "the past year" },
];

export default function SearchBar({
  onInvestigate,
  onDeepResearch,
  isLoading,
  loadingLabel = "Framing…",
  disabled = false,
  windowLabel = "1d",
}: SearchBarProps) {
  const runWindow =
    INVESTIGATE_WINDOWS.find((w) => w.value === windowLabel)?.label ?? "1D";
  const groupId = useId();
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const locked = isLoading || disabled;

  const focusSearch = useCallback(() => inputRef.current?.focus(), []);
  useKeyPress(["Meta", "k"], focusSearch);
  useKeyPress(["Control", "k"], focusSearch);

  function validate(symbol: string): string | null {
    const clean = symbol.trim().toUpperCase();
    if (!clean) return "Enter a ticker to frame, for example NVDA.";
    if (clean.length > 5) return "Tickers are 5 characters or fewer.";
    if (!/^[A-Z0-9]+$/.test(clean))
      return "Use letters and numbers only — no spaces or symbols.";
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

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submitInvestigate(ticker);
  }

  return (
    <form onSubmit={handleSubmit} className="min-w-0">
      <div className="flex items-stretch gap-2">
        <label htmlFor="ticker-search" className="sr-only">
          Ticker to frame
        </label>
        <Bracketed
          snap
          className="flex min-w-0 flex-1 items-center gap-3 bg-panel px-3 py-2"
        >
          <span className="font-mono text-caution" aria-hidden>
            &gt;
          </span>
          <input
            id="ticker-search"
            ref={inputRef}
            type="text"
            value={ticker}
            onChange={(e) => {
              setTicker(e.target.value.toUpperCase());
              setError(null);
            }}
            placeholder="run ticker — e.g. NVDA"
            disabled={locked}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${groupId}-error` : undefined}
            className={cn(
              "min-w-0 flex-1 bg-transparent outline-none",
              "font-mono text-base uppercase tracking-wide text-caution",
              "placeholder:normal-case placeholder:tracking-wide placeholder:text-readout disabled:opacity-50"
            )}
          />
          <kbd className="osd-label hidden shrink-0 border border-hud px-1.5 py-0.5 lg:block">
            ⌘K
          </kbd>
        </Bracketed>

        <HudButton
          type="submit"
          variant="primary"
          loading={isLoading}
          disabled={disabled}
          className="min-h-11 shrink-0"
        >
          {isLoading ? loadingLabel : `Investigate ${runWindow}`}
        </HudButton>

        {onDeepResearch && (
          <button
            type="button"
            onClick={() => {
              const validationError = validate(ticker);
              if (validationError) {
                setError(validationError);
                return;
              }
              onDeepResearch(ticker.trim().toUpperCase());
            }}
            disabled={locked}
            title="Run the long-form research report instead of opening a case"
            className="cyber-chamfer-sm hidden shrink-0 border border-hud px-3 font-mono text-sm uppercase tracking-osd text-readout hover:border-magenta hover:text-magenta hover:shadow-magenta disabled:opacity-50 sm:block"
          >
            Full report
          </button>
        )}
      </div>

      {error && (
        <p
          id={`${groupId}-error`}
          role="alert"
          className="mt-1.5 font-mono text-[0.6875rem] uppercase tracking-wide text-down"
        >
          {error}
        </p>
      )}
    </form>
  );
}
