"use client";

import { useEffect, useState } from "react";
import { ApiError, fetchFilings } from "@/lib/api";
import type { FilingsData, SECOutput } from "@/types";

interface SECFilingsPanelProps {
  ticker: string;
  secOutput?: SECOutput | null;
}

export default function SECFilingsPanel({
  ticker,
  secOutput,
}: SECFilingsPanelProps) {
  const [filings, setFilings] = useState<FilingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchFilings(ticker)
      .then((data) => {
        if (!cancelled) setFilings(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Could not load SEC filings"
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  return (
    <div className="card-surface p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-amber-400/90">
            SEC Filings
          </h3>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-slate-500">
            Official documents filed with the U.S. SEC (10-K annual, 10-Q quarterly,
            8-K material events). Below: AI summary from the SEC agent plus raw
            filings from EDGAR.
          </p>
        </div>
        {secOutput?.latest_filing_type && (
          <span className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-sm font-medium text-amber-300">
            Latest: {secOutput.latest_filing_type}
          </span>
        )}
      </div>

      {secOutput && (
        <div className="mt-4 rounded-lg border border-[#1e1e2e] bg-[#0a0a0f]/50 p-4">
          <p className="text-xs font-medium uppercase text-slate-500">
            AI filing analysis
          </p>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            {secOutput.filing_summary}
          </p>
          {secOutput.risk_signals.length > 0 && (
            <ul className="mt-3 space-y-1">
              {secOutput.risk_signals.map((signal, i) => (
                <li key={i} className="text-sm text-slate-400">
                  <span className="text-amber-500/80">•</span> {signal}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="mt-4">
        <p className="text-xs font-medium uppercase text-slate-500">
          Recent filings (EDGAR)
        </p>

        {loading && (
          <p className="mt-3 text-sm text-slate-500">Loading filings…</p>
        )}

        {error && !loading && (
          <p className="mt-3 text-sm text-amber-300/90">{error}</p>
        )}

        {filings && !loading && (
          <>
            {filings.company_name && (
              <p className="mt-2 text-sm text-slate-400">
                {filings.company_name}
                {filings.cik ? ` · CIK ${filings.cik}` : ""}
              </p>
            )}
            {filings.filings.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">No recent filings found.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {filings.filings.map((filing) => (
                  <li
                    key={filing.accession_number}
                    className="flex flex-col gap-2 rounded-lg border border-[#1e1e2e] bg-[#0a0a0f]/40 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <span className="font-semibold text-amber-400">
                        {filing.form_type}
                      </span>
                      <span className="ml-2 text-sm text-slate-500">
                        Filed {filing.filing_date}
                        {filing.report_date
                          ? ` · Period ${filing.report_date}`
                          : ""}
                      </span>
                    </div>
                    <a
                      href={filing.document_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0 text-sm font-medium text-accent hover:underline"
                    >
                      View on SEC.gov →
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}
