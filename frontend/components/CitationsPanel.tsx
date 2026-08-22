"use client";

import { Bot, Globe, Landmark, LineChart } from "lucide-react";
import { Bracketed, ColumnHeader } from "@/components/hud/primitives";
import type { AnalysisResponse } from "@/types";

interface CitationsPanelProps {
  analysis: AnalysisResponse;
  analyzedAt?: string;
}

const SOURCES = [
  {
    id: "yahoo",
    name: "Yahoo Finance",
    icon: LineChart,
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
    icon: Landmark,
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
    icon: Globe,
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
    icon: Bot,
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
    <Bracketed
      as="section"
      large
      id="data-sources"
      className="scroll-mt-24 p-4 sm:p-5"
      aria-labelledby="data-sources-title"
    >
      <ColumnHeader
        aside={new Date(timestamp).toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        })}
      >
        <span id="data-sources-title">Where this came from</span>
      </ColumnHeader>

      <p className="mb-4 max-w-2xl text-xs leading-relaxed text-readout">
        Every figure above traces back to one of these {SOURCES.length} layers.
        Model synthesis is labelled as such and is never presented as a source.
      </p>

      <div className="grid gap-3 md:grid-cols-2">
        {SOURCES.map((source) => {
          const Icon = source.icon;
          return (
            <Bracketed key={source.id} quiet snap className="p-4">
              <div className="flex items-center gap-2.5 border-b border-hud pb-2.5">
                <Icon
                  className="h-4 w-4 shrink-0 text-readout"
                  strokeWidth={1.5}
                  aria-hidden
                />
                <span className="truncate font-display text-[0.6875rem] font-bold uppercase tracking-osd text-osd">
                  {source.name}
                </span>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-readout">
                {source.description(analysis)}
              </p>
              <ul className="mt-3 space-y-1.5">
                {source.dataPoints(analysis).map((point, i) => (
                  <li
                    key={i}
                    className="flex gap-2.5 break-words text-[0.6875rem] leading-relaxed text-osd/70"
                  >
                    <span
                      className="mt-1.5 h-1 w-1 shrink-0 bg-hud-line"
                      aria-hidden
                    />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </Bracketed>
          );
        })}
      </div>

      <p className="mt-4 text-[0.6875rem] leading-relaxed text-readout">
        Source coverage reflects what was reachable during this run. Model
        conclusions stay probabilistic — verify them against the receipts.
      </p>
    </Bracketed>
  );
}
