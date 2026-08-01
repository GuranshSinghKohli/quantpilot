"use client";

import type { AnalysisResponse } from "@/types";

interface CitationsPanelProps {
  analysis: AnalysisResponse;
  analyzedAt?: string;
}

const SOURCES = [
  {
    id: "yahoo",
    name: "Yahoo Finance",
    icon: "📈",
    accent: "border-l-emerald-500",
    description: (a: AnalysisResponse) =>
      `Market data for ${a.ticker}: price, fundamentals, and news headlines via yfinance.`,
    dataPoints: (a: AnalysisResponse) => [
      a.metrics_output?.valuation_rating
        ? `Valuation: ${a.metrics_output.valuation_rating}`
        : null,
      a.news_output?.sentiment
        ? `News sentiment: ${a.news_output.sentiment}`
        : null,
    ].filter(Boolean) as string[],
  },
  {
    id: "sec",
    name: "SEC EDGAR",
    icon: "🏛️",
    accent: "border-l-amber-500",
    description: (a: AnalysisResponse) =>
      `SEC filings are official reports U.S. public companies file with the Securities and Exchange Commission (10-K annual, 10-Q quarterly, 8-K material events). QuantPilot pulls recent filings for ${a.ticker} from EDGAR to extract disclosure and risk signals, not legal advice.`,
    dataPoints: (a: AnalysisResponse) => [
      a.sec_output?.latest_filing_type
        ? `Latest filing: ${a.sec_output.latest_filing_type}`
        : null,
      a.sec_output?.filing_summary
        ? a.sec_output.filing_summary.slice(0, 120) + "…"
        : null,
    ].filter(Boolean) as string[],
  },
  {
    id: "ir",
    name: "Investor Relations (Browser MCP)",
    icon: "🌐",
    accent: "border-l-cyan-500",
    description: (a: AnalysisResponse) => {
      const provider = a.ir_materials?.provider || "httpx/OpenClaw";
      return `Browser-grounded IR retrieval for ${a.ticker} via MCP (${provider}). Uses OpenClaw when configured; otherwise allowlisted HTTP fetches of investor pages.`;
    },
    dataPoints: (a: AnalysisResponse) => [
      a.ir_materials?.sources?.length
        ? `Sources: ${a.ir_materials.sources.slice(0, 2).join(" · ")}`
        : a.ir_materials?.error
          ? `IR note: ${a.ir_materials.error.slice(0, 100)}`
          : null,
      a.earnings_output?.sources?.length
        ? `Earnings cites: ${a.earnings_output.sources.length} URL(s)`
        : null,
    ].filter(Boolean) as string[],
  },
  {
    id: "openai",
    name: "OpenAI Analysis",
    icon: "🤖",
    accent: "border-l-accent",
    description: () =>
      "Multi-agent synthesis using GPT-4o-mini across news, financial, SEC, earnings, macro, risk, debate, and verification.",
    dataPoints: (a: AnalysisResponse) => [
      a.earnings_output?.tone
        ? `Earnings tone: ${a.earnings_output.tone}`
        : null,
      a.macro_output?.relevance
        ? `Macro relevance: ${a.macro_output.relevance}`
        : null,
      a.risk_output?.risk_level
        ? `Risk level: ${a.risk_output.risk_level}`
        : null,
      a.verification_output?.groundedness_score != null
        ? `Groundedness: ${(a.verification_output.groundedness_score * 100).toFixed(0)}%`
        : null,
      `Confidence: ${((a.risk_output?.confidence_score ?? 0) * 100).toFixed(0)}%`,
    ].filter(Boolean) as string[],
  },
];

export default function CitationsPanel({
  analysis,
  analyzedAt,
}: CitationsPanelProps) {
  const timestamp =
    analyzedAt ?? new Date().toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });

  return (
    <section
      id="data-sources"
      className="card-surface scroll-mt-24 overflow-hidden p-5 sm:p-6"
      aria-labelledby="data-sources-title"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-cyan-500/20 bg-cyan-500/[0.08] text-cyan-300">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              className="h-5 w-5"
              aria-hidden="true"
            >
              <path d="m12 3 8 4v5c0 4.6-3.1 7.5-8 9-4.9-1.5-8-4.4-8-9V7l8-4Z" />
              <path d="m9 12 2 2 4-4" />
            </svg>
          </span>
          <div>
            <h3
              id="data-sources-title"
              className="font-display text-base font-semibold text-white"
            >
              Data sources & provenance
            </h3>
            <p className="mt-1 max-w-xl text-xs leading-relaxed text-slate-500">
              Trace the market data, filings, browser research, and model
              synthesis behind this report.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="rounded-full border border-emerald-500/20 bg-emerald-500/[0.07] px-2.5 py-1 text-[10px] font-medium text-emerald-300">
            {SOURCES.length} source layers
          </span>
          <span className="rounded-full border border-white/[0.07] bg-white/[0.025] px-2.5 py-1 text-[10px] text-slate-500">
            {new Date(timestamp).toLocaleString("en-US", {
              month: "short",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </span>
        </div>
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {SOURCES.map((source) => (
          <div
            key={source.id}
            className={`group rounded-xl border border-white/[0.06] border-l-2 bg-white/[0.018] p-4 transition hover:border-white/[0.11] hover:bg-white/[0.03] ${source.accent}`}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="text-lg" aria-hidden="true">
                  {source.icon}
                </span>
                <span className="truncate text-sm font-semibold text-slate-200">
                  {source.name}
                </span>
              </div>
              <span className="flex shrink-0 items-center gap-1 text-[10px] font-medium text-emerald-400/80">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                used
              </span>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-slate-400">
              {source.description(analysis)}
            </p>
            <ul className="mt-3 space-y-1.5 border-t border-white/[0.05] pt-3">
              {source.dataPoints(analysis).map((point, i) => (
                <li
                  key={i}
                  className="flex gap-2 break-all text-[11px] leading-relaxed text-slate-500"
                >
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-600" />
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[10px] leading-relaxed text-slate-600">
        Source coverage reflects the data available during this run. Model
        conclusions remain probabilistic and should be independently verified.
      </p>
    </section>
  );
}
