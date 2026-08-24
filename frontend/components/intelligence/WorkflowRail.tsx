"use client";

import { cn } from "@/lib/utils";
import type { WorkflowStep } from "@/lib/investigationView";

export default function WorkflowRail({
  steps,
  currentTask,
}: {
  steps: WorkflowStep[];
  currentTask?: string;
}) {
  return (
    <section aria-label="Investigation workflow" className="bk p-4">
      <h2 className="font-display text-sm font-bold uppercase tracking-wide text-osd">
        Workflow
      </h2>
      {currentTask && (
        <p className="mt-2 text-sm text-osd/90">Current: {currentTask}</p>
      )}
      <ol className="mt-4 flex flex-col gap-0 sm:flex-row sm:flex-wrap sm:gap-x-2">
        {steps.map((step, i) => (
          <li key={step.id} className="flex items-center gap-2 py-1.5 sm:py-0">
            <span
              aria-hidden
              className={cn(
                "inline-flex h-5 w-5 items-center justify-center border font-mono text-xs",
                step.state === "done" && "border-caution bg-caution text-letterbox",
                step.state === "current" && "border-caution text-caution",
                step.state === "pending" && "border-hud text-readout",
                step.state === "skipped" && "border-hud text-readout/50"
              )}
            >
              {step.state === "done" ? "✓" : step.state === "current" ? "●" : "○"}
            </span>
            <span
              className={cn(
                "osd-label",
                step.state === "current" && "text-caution",
                step.state === "done" && "text-osd",
                step.state === "pending" && "text-readout",
                step.state === "skipped" && "text-readout/50"
              )}
            >
              {step.label}
            </span>
            {i < steps.length - 1 && (
              <span className="hidden text-hud sm:inline" aria-hidden>
                /
              </span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
