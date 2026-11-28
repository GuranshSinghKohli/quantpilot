import type { Claim, InvestigationDetail, InvestigationStatus } from "@/types";

export type WorkflowStepId =
  | "event"
  | "plan"
  | "hypotheses"
  | "research"
  | "verify"
  | "advocate"
  | "synthesis";

export type StepState = "done" | "current" | "pending" | "skipped";

export interface WorkflowStep {
  id: WorkflowStepId;
  label: string;
  state: StepState;
}

const ORDER: WorkflowStepId[] = [
  "event",
  "plan",
  "hypotheses",
  "research",
  "verify",
  "advocate",
  "synthesis",
];

const LABELS: Record<WorkflowStepId, string> = {
  event: "Event",
  plan: "Plan",
  hypotheses: "Hypotheses",
  research: "Research",
  verify: "Verify",
  advocate: "Devil's Advocate",
  synthesis: "Synthesis",
};

export function workflowFromCase(detail: InvestigationDetail | null): WorkflowStep[] {
  const status = String(detail?.status || "");
  const roster = detail?.roster || {};
  const graph = asRecord(roster.investigation_graph);
  const hasPlan = Boolean(graph.plan || graph.research_plan);
  const hasHyps = (detail?.claims?.length || 0) > 0 || Boolean(graph.hypotheses);
  const hasEvidence = (detail?.evidence_items?.length || 0) > 0;
  const hasVerify = Boolean(roster.verification_summary || graph.verification_report);
  const hasDa = Boolean(detail?.da_outcome || graph.da);
  const hasFinal = Boolean(roster.final_report);
  const complete = status === "complete";
  const skipped = status === "skipped_market_noise";

  let current: WorkflowStepId = "event";
  if (status === "planning") current = hasPlan ? "hypotheses" : "plan";
  else if (status === "collecting") current = "research";
  else if (status === "verifying") current = hasVerify && !hasDa ? "advocate" : "verify";
  else if (complete) current = "synthesis";
  else if (status === "failed") current = hasEvidence ? "verify" : "research";

  return ORDER.map((id) => {
    const reached =
      (id === "event") ||
      (id === "plan" && (hasPlan || status !== "planning")) ||
      (id === "hypotheses" && hasHyps) ||
      (id === "research" && hasEvidence) ||
      (id === "verify" && hasVerify) ||
      (id === "advocate" && hasDa) ||
      (id === "synthesis" && (hasFinal || complete));
    let state: StepState = "pending";
    if (skipped && id !== "event") state = "skipped";
    else if (id === current && !complete && !skipped) state = "current";
    else if (complete && reached) state = "done";
    else if (reached) state = "done";
    return { id, label: LABELS[id], state };
  });
}

export function currentTask(detail: InvestigationDetail | null): string {
  const status = String(detail?.status || "");
  const graph = asRecord(detail?.roster?.investigation_graph);
  const plan = asRecord(graph.research_plan);
  const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
  const running = tasks.find(
    (t) => asRecord(t).status === "running" && asRecord(t).objective
  );
  const pending = tasks.find(
    (t) => asRecord(t).status === "pending" && asRecord(t).objective
  );
  const live = asRecord(running || pending);
  if (typeof live.objective === "string" && live.objective) {
    return live.objective;
  }
  if (typeof plan.focus === "string" && plan.focus && status === "collecting") {
    return plan.focus;
  }
  if (status === "planning") return "Planning what evidence is needed";
  if (status === "collecting") return "Collecting cited evidence";
  if (status === "verifying") return "Verifying claims against evidence";
  if (status === "complete") return "Investigation complete";
  if (status === "failed") return "Investigation failed";
  if (status === "skipped_market_noise") return "Skipped as market noise";
  return "Waiting for a case";
}

export function whyInvestigated(detail: InvestigationDetail): string[] {
  const reasons: string[] = [];
  const trigger = String(detail.trigger_reason || "");
  if (trigger === "scheduled") reasons.push("Autonomous monitor");
  else if (trigger === "price_move") reasons.push("Price move trigger");
  else if (trigger === "alert") reasons.push("Alert trigger");
  else if (trigger === "on_demand") reasons.push("Opened on demand");
  else if (trigger) reasons.push(trigger.replace(/_/g, " "));
  const graph = asRecord(detail.roster?.investigation_graph);
  const event = asRecord(graph.event || graph);
  const materiality = event.materiality_score ?? graph.materiality_score;
  if (typeof materiality === "number" && materiality > 0) {
    reasons.push(`Materiality ${materiality.toFixed(2)}`);
  }
  const exposure = event.portfolio_exposure ?? graph.portfolio_exposure;
  if (typeof exposure === "number" && exposure > 0) {
    reasons.push(`${Math.round(exposure * 100)}% portfolio exposure`);
  }
  if (detail.window_label) reasons.push(`Window ${detail.window_label}`);
  return reasons;
}

export function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

export function supportLabel(claim: Claim): string {
  const n = (claim.evidence_links || []).filter((l) => l.relation === "supports").length;
  const c = (claim.evidence_links || []).filter((l) => l.relation === "contradicts").length;
  if (n >= 3 && c === 0) return "Strong";
  if (n >= 2) return "Medium";
  if (n >= 1) return "Limited";
  return "Unlinked";
}

export function formatMove(pct?: number | null): string {
  if (pct == null || !Number.isFinite(pct)) return "—";
  const sign = pct < 0 ? "−" : "+";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

export function formatWhen(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export function qualityBadge(item: {
  source_class?: string;
  source_quality?: string;
  source_type?: string;
}): string {
  const cls = (item.source_class || "").toUpperCase();
  if (cls.includes("PRIMARY") || item.source_type === "ir_page" || item.source_type === "filing") {
    return "PRIMARY SOURCE";
  }
  const q = (item.source_quality || "").toLowerCase();
  if (q.includes("high")) return "HIGH QUALITY";
  if (q.includes("context")) return "CONTEXT";
  return "SECONDARY";
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function stageFromStatus(status: string | InvestigationStatus): string {
  switch (status) {
    case "planning":
      return "Planning";
    case "collecting":
      return "Evidence collection";
    case "verifying":
      return "Verification";
    case "complete":
      return "Complete";
    case "failed":
      return "Failed";
    case "skipped_market_noise":
      return "Skipped";
    default:
      return String(status);
  }
}
