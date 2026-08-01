"use client";

import type { AgentStep } from "@/types";

const STEP_MESSAGES: Record<string, string> = {
  news: "Scanning current headlines and market sentiment",
  financial: "Calculating fundamentals and valuation signals",
  sec: "Reading recent SEC filings and disclosures",
  earnings: "Reviewing earnings and investor-relations material",
  macro: "Mapping rates, inflation, and macro conditions",
  risk: "Stress-testing the investment case",
  bull: "Building the strongest upside thesis",
  bear: "Challenging the thesis and surfacing downside",
  verification: "Checking claims against source evidence",
  report: "Synthesizing the final research report",
  memo: "Drafting a shareable investment memo",
};

interface LoadingPipelineProps {
  ticker?: string | null;
  currentStep?: AgentStep;
  completedCount: number;
  totalCount: number;
}

export default function LoadingPipeline({
  ticker,
  currentStep,
  completedCount,
  totalCount,
}: LoadingPipelineProps) {
  const progress = Math.max(
    4,
    Math.round((completedCount / Math.max(totalCount, 1)) * 100)
  );
  const message = currentStep
    ? STEP_MESSAGES[currentStep.id] ?? `${currentStep.name} is working`
    : `Connecting live data for ${ticker ?? "this ticker"}`;

  return (
    <section
      className="card-surface overflow-hidden p-5 sm:p-6"
      aria-live="polite"
      aria-label="Analysis progress"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="relative mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-violet-500/25 bg-violet-500/10">
            <span className="absolute h-2.5 w-2.5 animate-ping rounded-full bg-violet-400/40" />
            <span className="relative h-2 w-2 rounded-full bg-violet-300" />
          </span>
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
              {currentStep ? currentStep.name : "Preparing research"}
            </p>
            <p className="mt-1 text-sm font-medium text-slate-200">{message}</p>
          </div>
        </div>
        <span className="shrink-0 rounded-full border border-white/[0.07] bg-white/[0.03] px-2.5 py-1 text-[10px] tabular-nums text-slate-500">
          {completedCount}/{totalCount} agents
        </span>
      </div>

      <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-violet-500 via-fuchsia-400 to-cyan-400 transition-[width] duration-700 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
      <div className="mt-2 flex items-center justify-between text-[10px] text-slate-600">
        <span>Live pipeline</span>
        <span>{progress}% complete</span>
      </div>
    </section>
  );
}
