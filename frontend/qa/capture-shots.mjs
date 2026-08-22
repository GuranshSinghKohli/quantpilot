import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.SHOT_BASE || "http://127.0.0.1:3117/";
const OUT = "/Users/guranshkohli/quantpilot/.impeccable/shots/";
mkdirSync(OUT, { recursive: true });

/* Synthetic demonstration data — stands in for the live API so the populated
   composition can be inspected. Not shipped, not presented as real. */
const CLAIMS = [
  {
    id: 1,
    statement:
      "Updated export licensing guidance narrowed the addressable data-center channel, and the sell-side cut unit expectations within hours of the notice.",
    stance: "supports_move",
    confidence_score: 0.86,
    rank: 1,
    devil_advocate_notes:
      "The notice was public three days earlier, so some of the reaction may be a delayed repricing rather than new information.",
    evidence_links: [
      { id: 1, evidence_id: 1, relation: "supports", note: "" },
      { id: 2, evidence_id: 2, relation: "supports", note: "" },
      { id: 3, evidence_id: 3, relation: "supports", note: "" },
      { id: 4, evidence_id: 4, relation: "supports", note: "" },
    ],
    created_at: "2026-08-18T15:00:00Z",
  },
  {
    id: 2,
    statement:
      "Advanced packaging capacity is booked through the next two quarters, capping how fast shipments can respond to demand.",
    stance: "supports_move",
    confidence_score: 0.43,
    rank: 2,
    devil_advocate_notes: "",
    evidence_links: [{ id: 5, evidence_id: 5, relation: "supports", note: "" }],
    created_at: "2026-08-18T15:00:00Z",
  },
  {
    id: 3,
    statement:
      "Hyperscaler capital expenditure growth is normalising off a peak, which compresses the forward growth rate the multiple was built on.",
    stance: "neutral",
    confidence_score: 0.27,
    rank: 3,
    devil_advocate_notes: "",
    evidence_links: [{ id: 6, evidence_id: 6, relation: "context", note: "" }],
    created_at: "2026-08-18T15:00:00Z",
  },
  {
    id: 4,
    statement:
      "A competing accelerator is winning share in a narrow set of inference workloads.",
    stance: "contradicts",
    confidence_score: 0.15,
    rank: 4,
    devil_advocate_notes: "",
    evidence_links: [],
    created_at: "2026-08-18T15:00:00Z",
  },
  {
    id: 5,
    statement:
      "Broad multiple compression across large-cap growth accounts for part of the drawdown independent of company news.",
    stance: "neutral",
    confidence_score: 0.09,
    rank: 5,
    devil_advocate_notes: "",
    evidence_links: [],
    created_at: "2026-08-18T15:00:00Z",
  },
];

const EVIDENCE = [
  { id: 1, source_type: "news", retrieval_method: "yfinance", title: "Chip export curbs widened to additional jurisdictions", excerpt: "", source_url: "https://example.com/a", created_at: "2026-08-18T14:10:00Z" },
  { id: 2, source_type: "sec_filing", retrieval_method: "edgar", title: "8-K discloses a charge tied to revised licence requirements", excerpt: "", source_url: "https://www.sec.gov/edgar/search/", created_at: "2026-08-18T14:12:00Z" },
  { id: 3, source_type: "news", retrieval_method: "yfinance", title: "Channel checks point to paused orders at two large buyers", excerpt: "", source_url: "https://example.com/c", created_at: "2026-08-18T14:20:00Z" },
  { id: 4, source_type: "price", retrieval_method: "yfinance", title: "Session volume ran 2.4x the twenty-day average", excerpt: "", source_url: "", created_at: "2026-08-18T14:30:00Z" },
  { id: 5, source_type: "earnings", retrieval_method: "ir", title: "Packaging capacity commentary from the most recent call", excerpt: "", source_url: "https://example.com/e", created_at: "2026-08-18T14:35:00Z" },
  { id: 6, source_type: "macro", retrieval_method: "fred", title: "Capital expenditure guidance across large cloud operators", excerpt: "", source_url: "https://example.com/f", created_at: "2026-08-18T14:40:00Z" },
  { id: 7, source_type: "ir_page", retrieval_method: "openclaw", title: "Investor relations statement on supply commitments", excerpt: "", source_url: "https://example.com/g", created_at: "2026-08-18T14:45:00Z" },
  { id: 8, source_type: "news", retrieval_method: "yfinance", title: "Analyst note trims the price target but keeps the rating", excerpt: "", source_url: "https://example.com/h", created_at: "2026-08-18T14:50:00Z" },
];

const DETAIL = {
  id: 412,
  ticker: "NVDA",
  trigger_reason: "idiosyncratic",
  status: "complete",
  move_pct: -6.42,
  window_label: "1d",
  summary:
    "The move is best explained by tightened export licensing narrowing the addressable data-center channel, with packaging capacity limits as a secondary constraint. The Devil's Advocate pass found the licence notice was already public, so the leading claim is held at high but not settled confidence.",
  created_at: "2026-08-18T14:00:00Z",
  completed_at: "2026-08-18T15:00:00Z",
  claims_count: 5,
  evidence_count: 8,
  error_message: "",
  verification_notes: "",
  da_outcome: {
    outcome: "leading_weakened",
    counterargument:
      "If the licensing notice was public three sessions earlier, a single-day repricing of this size implies the market was not reading the original notice, which is possible but not demonstrated by the evidence collected here.",
    leading_weakened: true,
    reversal: false,
    citation_coverage: 0.8,
  },
  claims: CLAIMS,
  evidence_items: EVIDENCE,
};

const SUMMARIES = [
  { id: 412, ticker: "NVDA", trigger_reason: "idiosyncratic", status: "complete", move_pct: -6.42, window_label: "1d", summary: "", created_at: "2026-08-18T14:00:00Z", completed_at: null, claims_count: 5, evidence_count: 8 },
  { id: 411, ticker: "AVGO", trigger_reason: "vol_adjusted", status: "complete", move_pct: -4.18, window_label: "1d", summary: "", created_at: "2026-08-17T14:00:00Z", completed_at: null, claims_count: 4, evidence_count: 6 },
  { id: 410, ticker: "AMD", trigger_reason: "idiosyncratic", status: "complete", move_pct: -7.21, window_label: "1d", summary: "", created_at: "2026-08-16T14:00:00Z", completed_at: null, claims_count: 5, evidence_count: 7 },
  { id: 409, ticker: "QCOM", trigger_reason: "scheduled", status: "complete", move_pct: 3.11, window_label: "1w", summary: "", created_at: "2026-08-15T14:00:00Z", completed_at: null, claims_count: 3, evidence_count: 5 },
];

const PORTFOLIO = {
  holdings: [
    { ticker: "NVDA", current_price: 132.4, pe_ratio: 42.1, risk_level: "HIGH", valuation: "rich", weight_pct: 31, shares: 150, avg_cost: 146.2, market_value: 19875, unrealized_gain_pct: -9.42 },
    { ticker: "AVGO", current_price: 176.6, pe_ratio: 33.4, risk_level: "MEDIUM", valuation: "fair", weight_pct: 22, shares: 80, avg_cost: 184.1, market_value: 14128, unrealized_gain_pct: -4.18 },
    { ticker: "AMD", current_price: 86.8, pe_ratio: 51.2, risk_level: "HIGH", valuation: "rich", weight_pct: 27, shares: 200, avg_cost: 93.5, market_value: 17360, unrealized_gain_pct: -7.21 },
    { ticker: "QCOM", current_price: 108.7, pe_ratio: 18.9, risk_level: "LOW", valuation: "cheap", weight_pct: 20, shares: 120, avg_cost: 105.4, market_value: 13044, unrealized_gain_pct: 3.11 },
  ],
  avg_pe: 36.4,
  risk_mix: { HIGH: 2, MEDIUM: 1, LOW: 1 },
  sector_note: "Concentrated in semiconductors.",
  weakest_ticker: "AMD",
  summary:
    "Four holdings, all semiconductors. Concentration is the dominant risk here, not any single position.",
  disclaimer: "",
  total_market_value: 64407,
  weighted_by_real_positions: true,
};

const WATCHLIST = ["NVDA", "AVGO", "AMD", "QCOM"].map((t, i) => ({
  id: i + 1,
  ticker: t,
  created_at: "2026-08-10T00:00:00Z",
}));

async function stub(page, { signedIn = true, empty = false } = {}) {
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const url = route.request().url();
    const json = (body) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

    if (url.includes("/health")) return json({ status: "ok" });
    if (url.includes("/auth/me"))
      return signedIn
        ? json({ id: 1, email: "you@example.com", created_at: "2026-01-01T00:00:00Z" })
        : route.fulfill({ status: 401, contentType: "application/json", body: "{}" });
    if (url.includes("/investigations/search")) return json({ query: "", mode: "semantic", results: [] });
    if (route.request().method() === "POST" && url.includes("/investigations")) return json(DETAIL);
    if (/\/investigations\/\d+$/.test(url.split("?")[0])) return json(DETAIL);
    if (url.includes("/investigations")) return json(empty ? [] : SUMMARIES);
    if (url.includes("/portfolio/analyze")) return json(empty ? { ...PORTFOLIO, holdings: [] } : PORTFOLIO);
    if (url.includes("/watchlist")) return json(empty ? [] : WATCHLIST);
    if (url.includes("/history")) return json([]);
    if (url.includes("/alerts/unread")) return json(2);
    if (url.includes("/stock/")) return json({ ticker: "NVDA", current_price: 132.4, market_cap: 3.2e12, pe_ratio: 42.1, fifty_two_week_high: 195.6, fifty_two_week_low: 86.2 });
    return json({});
  });
}

const browser = await chromium.launch();

async function shot(name, viewport, opts = {}, prep) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2 });
  if (opts.signedIn !== false) {
    await ctx.addInitScript(() =>
      localStorage.setItem("quantpilot_token", "demo-token")
    );
  }
  const page = await ctx.newPage();
  await stub(page, opts);
  await page.goto(BASE, { waitUntil: "networkidle" });
  if (prep) await prep(page);
  await page.waitForTimeout(1400);
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: false });
  await page.screenshot({ path: `${OUT}${name}-full.png`, fullPage: true });
  await ctx.close();
  console.log("wrote", name);
}

const openCase = async (page) => {
  await page.getByRole("button", { name: /^NVDA$/ }).first().click().catch(() => {});
  await page.waitForTimeout(1200);
};

await shot("desktop-case", { width: 1440, height: 900 }, {}, openCase);
await shot("desktop-empty", { width: 1440, height: 900 }, { signedIn: false, empty: true });
await shot("mobile-case", { width: 390, height: 844 }, {}, openCase);
for (const y of [900, 1800, 2700, 3600]) {
  await shot(`mobile-case-${y}`, { width: 390, height: 844 }, {}, async (page) => {
    await openCase(page);
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(500);
  });
}

await browser.close();
