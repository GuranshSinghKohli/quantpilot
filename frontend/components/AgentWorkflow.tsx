"use client";

import type { AgentStep } from "@/types";

interface AgentWorkflowProps {
  steps: AgentStep[];
  isLoading: boolean;
  collapsed?: boolean;
}

const ICONS: Record<string, string> = {
  news: "📰",
  financial: "📊",
  sec: "📄",
  risk: "⚖️",
  report: "📝",
};

function StatusIndicator({ status }: { status: AgentStep["status"] }) {
  if (status === "complete") {
    return (
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400">
        ✓
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="pulse-agent flex h-8 w-8 items-center justify-center rounded-full border-2 border-accent bg-accent/20 text-accent">
        ●
      </span>
    );
  }
  return (
    <span className="flex h-8 w-8 items-center justify-center rounded-full border border-[#1e1e2e] bg-[#0a0a0f] text-slate-500">
      ○
    </span>
  );
}

export default function AgentWorkflow({
  steps,
  isLoading,
  collapsed = false,
}: AgentWorkflowProps) {
  if (collapsed && !isLoading) {
    const allComplete = steps.every((s) => s.status === "complete");
    if (allComplete) {
      return (
        <div className="card-surface flex items-center gap-2 px-4 py-3 opacity-80">
          <span className="text-emerald-400">✓</span>
          <span className="text-sm text-slate-400">
            All 5 agents completed analysis
          </span>
        </div>
      );
    }
  }

  return (
    <div className="card-surface p-6">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
        Agent Pipeline
      </h3>
      <div className="flex flex-col gap-0 md:flex-row md:items-center md:justify-between">
        {steps.map((step, index) => (
          <div key={step.id} className="flex flex-1 items-center">
            <div className="flex flex-col items-center text-center md:min-w-[100px]">
              <StatusIndicator status={step.status} />
              <span className="mt-2 text-lg">{ICONS[step.id] ?? "🤖"}</span>
              <span
                className={`mt-1 text-xs font-medium ${
                  step.status === "running"
                    ? "text-accent"
                    : step.status === "complete"
                      ? "text-emerald-400"
                      : "text-slate-500"
                }`}
              >
                {step.name}
              </span>
            </div>
            {index < steps.length - 1 && (
              <div
                className={`mx-2 hidden h-0.5 flex-1 md:block ${
                  step.status === "complete"
                    ? "bg-emerald-500/50"
                    : "bg-[#1e1e2e]"
                }`}
              />
            )}
            {index < steps.length - 1 && (
              <div
                className={`my-2 h-6 w-0.5 md:hidden ${
                  step.status === "complete"
                    ? "bg-emerald-500/50"
                    : "bg-[#1e1e2e]"
                }`}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
