"use client";

import { Bracketed } from "@/components/hud/primitives";
import type { AgentStep } from "@/types";

const STEP_MESSAGES: Record<string, string> = {
  news: "Scanning current headlines and market sentiment",
  financial: "Calculating fundamentals and valuation signals",
  sec: "Reading recent SEC filings and disclosures",
  earnings: "Reviewing earnings and investor-relations material",
  macro: "Mapping rates, inflation, and macro conditions",
  risk: "Stress-testing the research case",
  bull: "Building the strongest supporting case",
  bear: "Challenging the case and surfacing downside",
  verification: "Checking claims against source evidence",
  report: "Synthesizing the final research report",
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
    <Bracketed
      as="section"
      large
      className="p-4 sm:p-5"
      aria-live="polite"
      aria-label="Analysis progress"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2">
            <span
              className="h-1.5 w-1.5 shrink-0 animate-rec-blink bg-caution"
              aria-hidden
            />
            <span className="font-display text-[0.6875rem] font-bold uppercase tracking-osd text-caution">
              {currentStep ? currentStep.name : "Preparing research"}
            </span>
          </p>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-osd/90">
            {message}
          </p>
        </div>
        <span data-figures className="osd-readout shrink-0 text-readout">
          {completedCount}/{totalCount}
        </span>
      </div>

      {/* Segmented tape rather than a bar: the pipeline is a sequence of
          discrete passes, and the readout should say so. */}
      <div className="mt-5 flex gap-0.5" aria-hidden>
        {Array.from({ length: Math.max(totalCount, 1) }, (_, i) => (
          <span
            key={i}
            className={`h-2 flex-1 ${
              i < completedCount
                ? "bg-caution"
                : i === completedCount
                  ? "animate-rec-blink bg-caution/40"
                  : "bg-hud"
            }`}
          />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="osd-label">Running</span>
        <span data-figures className="osd-label">
          {progress}%
        </span>
      </div>
    </Bracketed>
  );
}
