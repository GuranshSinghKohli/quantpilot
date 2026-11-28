import assert from "node:assert/strict";
import test from "node:test";

import {
  citationView,
  formatReturn,
  isHttpUrl,
  pollResearch,
  researchModel,
  statusLabel,
} from "./researchDesk.ts";

const citation = {
  citation_id: "c1",
  kind: "document_excerpt",
  title: "Chip note",
  publisher: "Example Desk",
  published_at: "2026-09-15T16:05:00+00:00",
  source_url: "https://example.com/chip",
};

function payload(overrides: Record<string, unknown> = {}) {
  return {
    research_id: "job-1",
    status: "succeeded",
    request: { query: "What recent NVIDIA news coincided with market movement?" },
    plan: { source: "llm" },
    limitations: ["Market observations are descriptive and not causal."],
    verification_summary: { supported: 1, uncertain: 1, unsupported: 1, conflicting: 0 },
    claims: [
      { statement: "A source reported the headline.", claim_kind: "FACT", verification_status: "supported" },
      { statement: "NVDA returned over the window.", claim_kind: "OBSERVATION", verification_status: "supported" },
      { statement: "The partnership may widen.", claim_kind: "INFERENCE", verification_status: "uncertain" },
      { statement: "Buy the stock.", claim_kind: "FACT", verification_status: "unsupported" },
    ],
    report: {
      executive_summary: [
        { statement: "A source reported the headline.", finding_type: "FACT", citation_ids: ["c1"], agent: "news" },
      ],
      key_facts: [
        { statement: "A source reported the headline.", finding_type: "FACT", citation_ids: ["c1"], agent: "news" },
      ],
      market_observations: [
        { statement: "NVDA returned over the window.", finding_type: "OBSERVATION", citation_ids: ["m1"], agent: "market" },
      ],
      implications: [
        { statement: "The partnership may widen.", finding_type: "INFERENCE", citation_ids: ["c1"], agent: "company" },
      ],
      citations: [
        citation,
        {
          citation_id: "m1",
          kind: "market_fact",
          title: "NVDA v1",
          source_url: null,
          market: {
            symbol: "NVDA",
            configured_benchmark_symbol: "SPY",
            method: "session return relative to the configured benchmark; not a causal effect",
            windows: { "+1D": { return: 0.0218, relative_return: 0.012, benchmark_return: 0.0098 } },
            volume: { volume_ratio: 1.4 },
            volatility: { event_window_volatility: 0.02 },
          },
        },
      ],
    },
    agent_runs: [{ agent: "master", status: "succeeded", error: null, attempt: 1 }],
    error: null,
    ...overrides,
  };
}

test("http source urls are links and missing urls are not", () => {
  assert.equal(isHttpUrl("https://example.com/a"), true);
  assert.equal(isHttpUrl("http://example.com/a"), true);
  assert.equal(isHttpUrl("ftp://example.com"), false);
  assert.equal(isHttpUrl(null), false);
  const linked = citationView(citation);
  assert.equal(linked.href, "https://example.com/chip");
  const missing = citationView({ ...citation, source_url: null, citation_id: "c2" });
  assert.equal(missing.href, null);
});

test("market windows stay descriptive returns", () => {
  const view = citationView({
    citation_id: "m1",
    kind: "market_fact",
    title: "NVDA",
    source_url: null,
    market: {
      symbol: "NVDA",
      configured_benchmark_symbol: "SPY",
      windows: { "+1D": { return: 0.0218, relative_return: -0.004 } },
    },
  });
  assert.equal(view.href, null);
  assert.equal(view.market?.windows[0]?.assetReturn, "+2.18%");
  assert.equal(view.market?.windows[0]?.relativeReturn, "-0.40%");
  assert.match(view.market?.method || "", /not a causal effect/i);
  assert.equal(formatReturn(null), "unavailable");
});

test("succeeded report keeps types, verification, and excluded claims", () => {
  const model = researchModel(payload());
  assert.equal(model.statusLabel, "Research complete");
  assert.equal(model.findings[0]?.findingType, "FACT");
  assert.equal(model.findings[0]?.verification, "supported");
  assert.equal(model.findings[1]?.findingType, "OBSERVATION");
  assert.equal(model.findings[2]?.findingType, "INFERENCE");
  assert.equal(model.findings[2]?.verification, "uncertain");
  assert.equal(model.excluded[0]?.statement, "Buy the stock.");
  assert.equal(model.excluded[0]?.verification, "unsupported");
  assert.equal(model.citations[1]?.market?.benchmark, "SPY");
  assert.deepEqual(model.limitations, ["Market observations are descriptive and not causal."]);
});

test("partial, failed, and empty findings stay distinct", () => {
  assert.equal(statusLabel("partial"), "Research partial");
  assert.equal(statusLabel("failed"), "Research failed");
  assert.equal(statusLabel("queued"), "Queued");
  const empty = researchModel(payload({ status: "partial", report: { citations: [], limitations: [] }, claims: [], limitations: [] }));
  assert.equal(empty.findings.length, 0);
  assert.equal(empty.terminal, true);
  const failed = researchModel(payload({ status: "failed", error: "malformed", report: null, claims: [] }));
  assert.equal(failed.error, "malformed");
  assert.equal(failed.statusLabel, "Research failed");
});

test("polling stops on a terminal status and aborts", async () => {
  const states = [{ status: "queued", research_id: "job-1", request: { query: "q" } }, payload()];
  let calls = 0;
  const model = await pollResearch(async () => states[Math.min(calls++, states.length - 1)] as Record<string, unknown>, {
    intervalMs: 1,
    maxPolls: 5,
    sleepFn: async () => {},
  });
  assert.equal(calls, 2);
  assert.equal(model.status, "succeeded");

  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    () => pollResearch(async () => ({ status: "running" }), { signal: controller.signal, sleepFn: async () => {} }),
    /aborted/i
  );
});

test("submit sends the query and surfaces an API error", async () => {
  const { submitBody, submitResearch } = await import("./researchSubmit.ts");
  assert.deepEqual(submitBody("  NVIDIA news  "), { query: "NVIDIA news" });
  assert.throws(() => submitBody("   "), /query/);
  const ok = await submitResearch(async () => new Response(JSON.stringify({ research_id: "job-9", status: "queued", status_url: "/api/research/job-9" }), { status: 202 }), "http://api.test/api/research", "NVIDIA news");
  assert.equal(ok.research_id, "job-9");
  assert.equal(ok.status, "queued");
  await assert.rejects(
    () => submitResearch(async () => new Response("no", { status: 500 }), "http://api.test/api/research", "NVIDIA news"),
    /500/
  );
});
