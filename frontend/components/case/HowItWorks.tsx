"use client";

import { Bracketed, ColumnHeader } from "@/components/hud/primitives";

const STEPS = [
  {
    n: "1",
    text: "Investigate: planner picks tools, then ranker → verification → Devil's Advocate. Linear Python runner, not LangGraph.",
  },
  {
    n: "2",
    text: "Full report: LangGraph StateGraph, 11 nodes after fetch — news through memo — shared state, sequential edges.",
  },
  {
    n: "3",
    text: "Each claim keeps evidence links. Unverified rows stay marked. DA can demote the lead.",
  },
];

export default function HowItWorks() {
  return (
    <Bracketed as="section" aria-label="How the graph runs" className="p-3 sm:p-4">
      <ColumnHeader>Orchestration</ColumnHeader>

      <ol className="space-y-3">
        {STEPS.map((step) => (
          <li key={step.n} className="flex gap-3">
            <span
              data-figures
              className="flex h-6 w-6 shrink-0 items-center justify-center border border-caution font-mono text-xs text-caution"
            >
              {step.n}
            </span>
            <span className="text-sm leading-relaxed text-osd">{step.text}</span>
          </li>
        ))}
      </ol>

      <dl className="mt-5 space-y-2.5 border-t border-hud pt-4">
        <div className="flex items-center gap-3">
          <dt className="h-2 w-8 shrink-0 bg-down" aria-hidden />
          <dd className="text-sm leading-relaxed text-readout">
            Red mark: no evidence_id linked yet.
          </dd>
        </div>
        <div className="flex items-center gap-3">
          <dt className="shrink-0 border border-down px-2 py-0.5 font-mono text-xs uppercase tracking-osd text-down">
            Demoted
          </dt>
          <dd className="text-sm leading-relaxed text-readout">
            Leading claim lost rank after the DA pass.
          </dd>
        </div>
        <div className="flex items-center gap-3">
          <dt className="shrink-0" aria-hidden>
            <span className="block h-1.5 w-9 overflow-hidden bg-hud-deep">
              <span className="block h-full w-1/2 bg-osd" />
            </span>
          </dt>
          <dd className="text-sm leading-relaxed text-readout">
            Bar is the ranker confidence on that claim.
          </dd>
        </div>
      </dl>
    </Bracketed>
  );
}
