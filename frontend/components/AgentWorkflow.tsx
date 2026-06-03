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
  bull: "🐂",
  bear: "🐻",
  report: "📝",
};

function StatusIndicator({ status }: { status: AgentStep["status"] }) {
  if (status === "complete") {
    return (
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
        ✓
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="pulse-agent flex h-8 w-8 items-center justify-center rounded-full border-2 border-violet-400 bg-violet-500/20 text-violet-300">
        ●
      </span>
    );
  }
  return (
    <span className="flex h-8 w-8 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.02] text-slate-600">
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
        <div className="card-surface flex items-center gap-3 px-4 py-3">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-500/15 text-sm text-emerald-400">
            ✓
          </span>
          <span className="text-sm text-slate-400">
            all <span className="font-medium text-emerald-400/90">7 agents</span> finished — report ready
          </span>
        </div>
      );
    }
  }

  return (
    <div className="card-surface p-6">
      <h3 className="panel-title mb-4">agent pipeline</h3>
      <div className="flex flex-col gap-0 md:flex-row md:items-center md:justify-between">
        {steps.map((step, index) => (
          <div key={step.id} className="flex flex-1 items-center">
            <div className="flex flex-col items-center text-center md:min-w-[72px]">
              <StatusIndicator status={step.status} />
              <span className="mt-2 text-base">{ICONS[step.id] ?? "🤖"}</span>
              <span
                className={`mt-1 text-[10px] font-medium leading-tight ${
                  step.status === "running"
                    ? "text-violet-300"
                    : step.status === "complete"
                      ? "text-emerald-400/90"
                      : "text-slate-600"
                }`}
              >
                {step.name.replace(" Agent", "")}
              </span>
            </div>
            {index < steps.length - 1 && (
              <div
                className={`mx-1 hidden h-0.5 flex-1 md:block ${
                  step.status === "complete"
                    ? "bg-gradient-to-r from-emerald-500/40 to-violet-500/20"
                    : "bg-white/[0.06]"
                }`}
              />
            )}
            {index < steps.length - 1 && (
              <div
                className={`my-2 h-6 w-0.5 md:hidden ${
                  step.status === "complete"
                    ? "bg-emerald-500/40"
                    : "bg-white/[0.06]"
                }`}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
