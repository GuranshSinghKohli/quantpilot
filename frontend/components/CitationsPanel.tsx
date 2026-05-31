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
      `SEC filings are official reports U.S. public companies file with the Securities and Exchange Commission (10-K annual, 10-Q quarterly, 8-K material events). QuantPilot pulls recent filings for ${a.ticker} from EDGAR to extract disclosure and risk signals — not legal advice.`,
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
    id: "openai",
    name: "OpenAI Analysis",
    icon: "🤖",
    accent: "border-l-accent",
    description: () =>
      "Multi-agent synthesis using GPT-4o-mini across news, financial, SEC, and risk dimensions.",
    dataPoints: (a: AnalysisResponse) => [
      a.risk_output?.risk_level
        ? `Risk level: ${a.risk_output.risk_level}`
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
    <div className="card-surface p-6">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
        Data Sources & Provenance
      </h3>
      <p className="mt-1 text-xs text-slate-500">
        All analysis grounded in real financial data — not hallucinated
      </p>

      <div className="mt-4 space-y-4">
        {SOURCES.map((source) => (
          <div
            key={source.id}
            className={`rounded-lg border border-[#1e1e2e] border-l-4 bg-[#0a0a0f]/50 p-4 ${source.accent}`}
          >
            <div className="flex items-center gap-2">
              <span className="text-xl">{source.icon}</span>
              <span className="font-semibold text-slate-200">{source.name}</span>
            </div>
            <p className="mt-2 text-sm text-slate-400">
              {source.description(analysis)}
            </p>
            <ul className="mt-2 space-y-1">
              {source.dataPoints(analysis).map((point, i) => (
                <li key={i} className="text-xs text-slate-500">
                  • {point}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-600">Pulled: {timestamp}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
