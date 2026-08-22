"use client";

import { cn } from "@/lib/utils";
import type { AgentStep } from "@/types";

interface AgentWorkflowProps {
  steps: AgentStep[];
  isLoading: boolean;
  collapsed?: boolean;
}

/** The pipeline reads as recorded tape: filled segments are laid down. */
export default function AgentWorkflow({
  steps,
  isLoading,
  collapsed = false,
}: AgentWorkflowProps) {
  const complete = steps.filter((s) => s.status === "complete").length;
  const running = steps.find((s) => s.status === "running");

  if (collapsed && !isLoading && complete === steps.length) {
    return (
      <p className="osd-readout text-readout">
        All {steps.length} stages recorded.{" "}
        <span className="text-caution">Report ready.</span>
      </p>
    );
  }

  return (
    <div>
      <div
        className="flex items-stretch gap-px"
        role="img"
        aria-label={`Pipeline stage ${complete} of ${steps.length}${
          running ? `, running ${running.name}` : ""
        }`}
      >
        {steps.map((step) => (
          <span
            key={step.id}
            className={cn(
              "h-8 flex-1",
              step.status === "complete" && "bg-caution",
              step.status === "running" &&
                "bg-caution/40 animate-rec-blink",
              step.status === "waiting" && "bg-hud"
            )}
          />
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="osd-readout">
          {running ? (
            <>
              <span className="text-caution">Recording </span>
              {running.name.replace(" Agent", "")}
            </>
          ) : complete === steps.length ? (
            <span className="text-caution">All stages recorded</span>
          ) : (
            <span className="text-readout">Standing by</span>
          )}
        </p>
        <p data-figures className="osd-label">
          {String(complete).padStart(2, "0")} / {steps.length}
        </p>
      </div>

      <ol className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-4">
        {steps.map((step) => (
          <li
            key={step.id}
            className={cn(
              "flex items-center gap-2 text-[0.6875rem] uppercase tracking-osd",
              step.status === "complete" && "text-osd/70",
              step.status === "running" && "text-caution",
              step.status === "waiting" && "text-readout"
            )}
          >
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0",
                step.status === "complete" && "bg-caution",
                step.status === "running" && "bg-caution animate-rec-blink",
                step.status === "waiting" && "bg-hud-line"
              )}
              aria-hidden
            />
            {step.name.replace(" Agent", "")}
          </li>
        ))}
      </ol>
    </div>
  );
}
