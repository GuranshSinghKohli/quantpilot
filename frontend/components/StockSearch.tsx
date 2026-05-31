"use client";

import { FormEvent, useState } from "react";

type StockData = {
  ticker: string;
  current_price: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
};

type Filing = {
  form_type: string;
  filing_date: string;
  report_date: string | null;
  accession_number: string;
  document_url: string;
};

type FilingsData = {
  ticker: string;
  cik: string;
  company_name: string | null;
  filings: Filing[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function formatCurrency(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatLargeNumber(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatRatio(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

async function parseError(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({}));
  const detail = body?.detail ?? `Request failed (${response.status})`;
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

export default function StockSearch() {
  const [ticker, setTicker] = useState("");
  const [stock, setStock] = useState<StockData | null>(null);
  const [filings, setFilings] = useState<FilingsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filingsError, setFilingsError] = useState<string | null>(null);

  async function handleSearch(event: FormEvent) {
    event.preventDefault();
    const symbol = ticker.trim().toUpperCase();
    if (!symbol) {
      setError("Please enter a ticker symbol.");
      return;
    }

    setLoading(true);
    setError(null);
    setFilingsError(null);
    setStock(null);
    setFilings(null);

    try {
      const [stockResponse, filingsResponse] = await Promise.all([
        fetch(`${API_URL}/api/stocks/${symbol}`),
        fetch(`${API_URL}/api/filings/${symbol}`),
      ]);

      if (!stockResponse.ok) {
        throw new Error(await parseError(stockResponse));
      }

      const stockData: StockData = await stockResponse.json();
      setStock(stockData);

      if (!filingsResponse.ok) {
        setFilingsError(await parseError(filingsResponse));
      } else {
        const filingsData: FilingsData = await filingsResponse.json();
        setFilings(filingsData);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8">
      <form onSubmit={handleSearch} className="flex gap-3">
        <input
          type="text"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="e.g. AAPL"
          className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2.5 text-sm outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/30"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading && (
        <p className="text-sm text-slate-400">Fetching stock data and SEC filings…</p>
      )}

      {stock && !loading && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6">
          <h2 className="text-xl font-semibold">{stock.ticker}</h2>
          <dl className="mt-6 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-500">Current price</dt>
              <dd className="mt-1 text-lg font-medium">
                {formatCurrency(stock.current_price)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Market cap</dt>
              <dd className="mt-1 text-lg font-medium">
                {formatLargeNumber(stock.market_cap)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">P/E ratio</dt>
              <dd className="mt-1 text-lg font-medium">
                {formatRatio(stock.pe_ratio)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">52-week high</dt>
              <dd className="mt-1 text-lg font-medium">
                {formatCurrency(stock.fifty_two_week_high)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">52-week low</dt>
              <dd className="mt-1 text-lg font-medium">
                {formatCurrency(stock.fifty_two_week_low)}
              </dd>
            </div>
          </dl>
        </div>
      )}

      {stock && !loading && filingsError && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          SEC filings unavailable: {filingsError}
        </div>
      )}

      {stock && !loading && filings && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6">
          <h3 className="text-lg font-semibold">Recent SEC filings</h3>
          {filings.company_name && (
            <p className="mt-1 text-sm text-slate-400">{filings.company_name}</p>
          )}
          <ul className="mt-4 space-y-3">
            {filings.filings.map((filing) => (
              <li
                key={filing.accession_number}
                className="flex flex-wrap items-baseline justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-3 text-sm"
              >
                <div>
                  <span className="font-medium text-emerald-400">{filing.form_type}</span>
                  <span className="ml-2 text-slate-500">filed {filing.filing_date}</span>
                  {filing.report_date && (
                    <span className="ml-2 text-slate-600">
                      (period {filing.report_date})
                    </span>
                  )}
                </div>
                <a
                  href={filing.document_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-emerald-500 hover:text-emerald-400"
                >
                  View →
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
