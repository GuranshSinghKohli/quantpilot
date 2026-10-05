/** Display model for a research job. Numbers come from the API payload. */

export const POLL_INTERVAL_MS = 2000;
export const MAX_POLLS = 120;

export const TERMINAL_STATUSES = new Set([
  "succeeded",
  "partial",
  "failed",
  "cancelled",
]);

const FINDING_SECTIONS = [
  "executive_summary",
  "key_facts",
  "market_observations",
  "company_relationships",
  "technology_findings",
  "implications",
  "scenarios",
  "conflicts",
] as const;

export interface ResearchSubmit {
  research_id: string;
  status: string;
  status_url?: string;
}

export interface CitationView {
  citationId: string;
  kind: string;
  title: string;
  publisher: string | null;
  publishedAt: string | null;
  href: string | null;
  market: MarketView | null;
}

export interface MarketWindowView {
  label: string;
  assetReturn: string;
  relativeReturn: string;
  benchmarkReturn: string;
}

export interface MarketView {
  symbol: string;
  benchmark: string;
  method: string;
  eventTradingDate: string | null;
  windows: MarketWindowView[];
  volumeRatio: string | null;
  volatility: string | null;
}

export interface FindingView {
  statement: string;
  findingType: string;
  verification: string;
  citationIds: string[];
  agent: string;
}

export interface ResearchDeskModel {
  researchId: string;
  query: string;
  status: string;
  statusLabel: string;
  terminal: boolean;
  summary: FindingView[];
  findings: FindingView[];
  excluded: FindingView[];
  citations: CitationView[];
  limitations: string[];
  agents: { agent: string; status: string; error: string | null; attempt: number | null }[];
  planSource: string | null;
  error: string | null;
  counts: Record<string, number>;
}

export function isHttpUrl(value: unknown): value is string {
  return typeof value === "string" && (value.startsWith("https://") || value.startsWith("http://"));
}

export function statusLabel(status: string): string {
  if (status === "succeeded") return "Research complete";
  if (status === "partial") return "Research partial";
  if (status === "failed") return "Research failed";
  if (status === "cancelled") return "Research cancelled";
  if (status === "running") return "Running";
  if (status === "queued" || status === "publish_pending") return "Queued";
  return status || "Unknown";
}

export function formatReturn(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "unavailable";
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function formatRatio(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value.toFixed(2);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asList(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => asRecord(item) !== null);
}

function marketView(raw: unknown): MarketView | null {
  const market = asRecord(raw);
  if (!market) return null;
  const windows = asRecord(market.windows) ?? {};
  const order = ["-1D", "+1D", "+3D", "+5D", "+20D"];
  const rendered: MarketWindowView[] = [];
  for (const label of order) {
    const block = asRecord(windows[label]);
    if (!block) continue;
    rendered.push({
      label,
      assetReturn: formatReturn(block.return),
      relativeReturn: formatReturn(block.relative_return),
      benchmarkReturn: formatReturn(block.benchmark_return),
    });
  }
  const volume = asRecord(market.volume);
  const volatility = asRecord(market.volatility);
  return {
    symbol: String(market.symbol || "symbol unavailable"),
    benchmark: String(market.configured_benchmark_symbol || "configured benchmark"),
    method: String(
      market.method || "Session return relative to the configured benchmark. Not a causal effect."
    ),
    eventTradingDate: typeof market.event_trading_date === "string" ? market.event_trading_date : null,
    windows: rendered,
    volumeRatio: formatRatio(volume?.volume_ratio),
    volatility: formatRatio(volatility?.event_window_volatility),
  };
}

export function citationView(raw: Record<string, unknown>): CitationView {
  return {
    citationId: String(raw.citation_id || ""),
    kind: String(raw.kind || "citation"),
    title: String(raw.title || "Untitled source"),
    publisher: typeof raw.publisher === "string" && raw.publisher.trim() ? raw.publisher : null,
    publishedAt: typeof raw.published_at === "string" ? raw.published_at : null,
    href: isHttpUrl(raw.source_url) ? raw.source_url : null,
    market: raw.kind === "market_fact" ? marketView(raw.market) : null,
  };
}

function findingFrom(raw: Record<string, unknown>, verification: string): FindingView {
  const ids = Array.isArray(raw.citation_ids)
    ? raw.citation_ids.map((item) => String(item))
    : [];
  return {
    statement: String(raw.statement || ""),
    findingType: String(raw.finding_type || raw.claim_kind || "FINDING"),
    verification,
    citationIds: ids,
    agent: String(raw.agent || ""),
  };
}

export function researchModel(payload: Record<string, unknown>): ResearchDeskModel {
  const request = asRecord(payload.request);
  const report = asRecord(payload.report);
  const plan = asRecord(payload.plan);
  const claims = asList(payload.claims);
  const verificationByStatement = new Map<string, string>();
  for (const claim of claims) {
    const statement = String(claim.statement || "");
    const status = String(claim.verification_status || "");
    if (statement && status) verificationByStatement.set(statement, status);
  }
  const summary = asList(report?.executive_summary).map((item) =>
    findingFrom(item, verificationByStatement.get(String(item.statement || "")) || "supported")
  );
  const findings: FindingView[] = [];
  for (const section of FINDING_SECTIONS) {
    if (section === "executive_summary") continue;
    for (const item of asList(report?.[section])) {
      const statement = String(item.statement || "");
      findings.push(
        findingFrom(item, verificationByStatement.get(statement) || sectionStatus(section))
      );
    }
  }
  const shown = new Set(findings.map((item) => item.statement));
  const excluded = claims
    .filter((claim) => {
      const status = String(claim.verification_status || "");
      return status === "unsupported" || status === "conflicting";
    })
    .filter((claim) => !shown.has(String(claim.statement || "")))
    .map((claim) => findingFrom(claim, String(claim.verification_status)));
  const limitations = Array.isArray(payload.limitations)
    ? payload.limitations.map((item) => String(item)).filter(Boolean)
    : asList(report?.limitations).map((item) => String(item));
  const reportLimitations = Array.isArray(report?.limitations)
    ? report.limitations.map((item) => String(item)).filter(Boolean)
    : [];
  const mergedLimits = [...limitations];
  for (const item of reportLimitations) {
    if (!mergedLimits.includes(item)) mergedLimits.push(item);
  }
  const counts = asRecord(payload.verification_summary) ?? {};
  return {
    researchId: String(payload.research_id || ""),
    query: String(request?.query || ""),
    status: String(payload.status || ""),
    statusLabel: statusLabel(String(payload.status || "")),
    terminal: TERMINAL_STATUSES.has(String(payload.status || "")),
    summary,
    findings,
    excluded,
    citations: asList(report?.citations).map(citationView),
    limitations: mergedLimits,
    agents: asList(payload.agent_runs).map((row) => ({
      agent: String(row.agent || ""),
      status: String(row.status || ""),
      error: typeof row.error === "string" ? row.error : null,
      attempt: typeof row.attempt === "number" ? row.attempt : null,
    })),
    planSource: typeof plan?.source === "string" ? plan.source : null,
    error: typeof payload.error === "string" ? payload.error : null,
    counts: {
      supported: Number(counts.supported || 0),
      uncertain: Number(counts.uncertain || 0),
      unsupported: Number(counts.unsupported || 0),
      conflicting: Number(counts.conflicting || 0),
    },
  };
}

function sectionStatus(section: string): string {
  if (section === "implications" || section === "scenarios") return "uncertain";
  if (section === "conflicts") return "conflicting";
  return "supported";
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("The operation was aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("The operation was aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function pollResearch(
  load: () => Promise<Record<string, unknown>>,
  options: { intervalMs?: number; maxPolls?: number; signal?: AbortSignal; sleepFn?: typeof sleep } = {}
): Promise<ResearchDeskModel> {
  const intervalMs = options.intervalMs ?? POLL_INTERVAL_MS;
  const maxPolls = options.maxPolls ?? MAX_POLLS;
  const wait = options.sleepFn ?? sleep;
  let last: ResearchDeskModel | null = null;
  for (let poll = 0; poll < maxPolls; poll += 1) {
    if (options.signal?.aborted) {
      throw new DOMException("The operation was aborted", "AbortError");
    }
    last = researchModel(await load());
    if (last.terminal) return last;
    await wait(intervalMs, options.signal);
  }
  if (!last) throw new Error("research status was not returned");
  return last;
}
