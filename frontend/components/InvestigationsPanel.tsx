"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import EvidenceReport from "@/components/EvidenceReport";
import InvestigationChatPanel from "@/components/InvestigationChatPanel";
import {
  addInvestigationClaim,
  addInvestigationEvidence,
  ApiError,
  completeInvestigation,
  fetchInvestigation,
  fetchInvestigations,
  runInvestigation,
  searchInvestigations,
  smartSummarizeInvestigation,
  sweepInvestigations,
} from "@/lib/api";
import type {
  InvestigationDetail,
  InvestigationSearchHit,
  InvestigationSummary,
  SmartSummary,
} from "@/types";

interface InvestigationsPanelProps {
  selectedId: number | null;
  onSelectedChange: (id: number | null, ticker?: string) => void;
  onDeepResearch?: (ticker: string) => void;
  isResearchLoading?: boolean;
  isPipelineLoading?: boolean;
  /** Signed-in users can scan holdings with Phase 11 triggers. */
  canScanHoldings?: boolean;
}

function statusTone(status: string): string {
  switch (status) {
    case "complete":
      return "text-emerald-300 bg-emerald-500/10 border-emerald-500/30";
    case "skipped_market_noise":
      return "text-slate-300 bg-slate-500/10 border-slate-500/30";
    case "failed":
      return "text-red-300 bg-red-500/10 border-red-500/30";
    case "verifying":
    case "collecting":
      return "text-cyan-300 bg-cyan-500/10 border-cyan-500/30";
    default:
      return "text-violet-300 bg-violet-500/10 border-violet-500/30";
  }
}

function humanWindow(label: string | undefined): string {
  switch ((label || "1d").toLowerCase()) {
    case "1d":
    case "intraday":
      return "1 day";
    case "1w":
    case "5d":
      return "1 week";
    case "1mo":
      return "1 month";
    case "6mo":
      return "6 months";
    case "1y":
      return "1 year";
    case "ytd":
      return "year to date";
    default:
      return label || "the selected window";
  }
}

/** Recover a % move from summary text when move_pct was not persisted. */
function parseMovePctFromText(text: string | undefined | null): number | null {
  if (!text) return null;
  const fell = text.match(/\bfell\s+(\d+(?:\.\d+)?)\s*%/i);
  if (fell) return -Math.abs(parseFloat(fell[1]));
  const rose = text.match(/\brose\s+(\d+(?:\.\d+)?)\s*%/i);
  if (rose) return Math.abs(parseFloat(rose[1]));
  const signed = text.match(/\b([+-]\d+(?:\.\d+)?)\s*%\s+over\b/i);
  if (signed) return parseFloat(signed[1]);
  const down = text.match(/\bdown\s+(\d+(?:\.\d+)?)\s*%/i);
  if (down) return -Math.abs(parseFloat(down[1]));
  const up = text.match(/\bup\s+(\d+(?:\.\d+)?)\s*%/i);
  if (up) return Math.abs(parseFloat(up[1]));
  return null;
}

function resolveMovePct(
  movePct: number | null | undefined,
  summary?: string | null
): number | null {
  if (movePct != null && !Number.isNaN(movePct)) return movePct;
  return parseMovePctFromText(summary);
}

function moveHeadline(
  ticker: string,
  movePct: number | null | undefined,
  windowLabel: string | undefined
): { verb: string; pctLine: string; tone: string } {
  const window = humanWindow(windowLabel);
  if (movePct == null || Number.isNaN(movePct)) {
    return {
      verb: "moved",
      pctLine: `Move over ${window} is still being measured`,
      tone: "text-slate-200",
    };
  }
  const abs = Math.abs(movePct).toFixed(1);
  if (movePct < -0.05) {
    return {
      verb: "fell",
      pctLine: `${ticker} fell ${abs}% over ${window}`,
      tone: "text-red-300",
    };
  }
  if (movePct > 0.05) {
    return {
      verb: "rose",
      pctLine: `${ticker} rose ${abs}% over ${window}`,
      tone: "text-emerald-300",
    };
  }
  return {
    verb: "was flat",
    pctLine: `${ticker} was roughly flat (${movePct >= 0 ? "+" : ""}${movePct.toFixed(1)}%) over ${window}`,
    tone: "text-slate-200",
  };
}

const CLAIM_AGENTS = [
  "Catalyst agent",
  "Fundamentals agent",
  "Macro / sector agent",
  "Flow / sentiment agent",
  "Risk agent",
] as const;

const PIPELINE_AGENTS = [
  { name: "Move detector", job: "Measure how far price moved in the window" },
  { name: "Planner", job: "Decide which tools and angles to pull" },
  { name: "Collector", job: "Gather news, filings, and price evidence" },
  { name: "Hypothesis ranker", job: "Score competing reasons for the move" },
  { name: "Verifier", job: "Check claims against receipts" },
  { name: "Devil's Advocate", job: "Try to overturn the leading explanation" },
] as const;

export default function InvestigationsPanel({
  selectedId,
  onSelectedChange,
  onDeepResearch,
  isResearchLoading = false,
  isPipelineLoading = false,
  canScanHoldings = false,
}: InvestigationsPanelProps) {
  const [items, setItems] = useState<InvestigationSummary[]>([]);
  const [selected, setSelected] = useState<InvestigationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [evidenceTitle, setEvidenceTitle] = useState("");
  const [evidenceExcerpt, setEvidenceExcerpt] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [claimStatement, setClaimStatement] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<InvestigationSearchHit[]>([]);
  const [searchMode, setSearchMode] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [smartSummary, setSmartSummary] = useState<SmartSummary | null>(null);
  const [summarizing, setSummarizing] = useState(false);

  const refreshList = useCallback(async () => {
    try {
      const list = await fetchInvestigations();
      setItems(list);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load investigations"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(
    async (id: number, announce = true) => {
      setError(null);
      setBusy(true);
      try {
        const detail = await fetchInvestigation(id);
        setSelected(detail);
        if (announce) onSelectedChange(detail.id, detail.ticker);
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "Failed to open investigation"
        );
      } finally {
        setBusy(false);
      }
    },
    [onSelectedChange]
  );

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    if (selectedId == null) {
      setSelected(null);
      setSmartSummary(null);
      return;
    }
    if (selected?.id === selectedId) return;
    setSmartSummary(null);
    void loadDetail(selectedId, false);
    void refreshList();
  }, [selectedId, selected?.id, loadDetail, refreshList]);

  async function handleSmartSummarize() {
    if (!selected) return;
    setSummarizing(true);
    setError(null);
    try {
      const summary = await smartSummarizeInvestigation(selected.id);
      setSmartSummary(summary);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Smart Summarize failed. Try again."
      );
    } finally {
      setSummarizing(false);
    }
  }

  async function handleAddEvidence(e: FormEvent) {
    e.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await addInvestigationEvidence(selected.id, {
        source_type: "other",
        retrieval_method: "user",
        title: evidenceTitle || "Manual evidence",
        excerpt: evidenceExcerpt,
        source_url: evidenceUrl.trim(),
      });
      setEvidenceTitle("");
      setEvidenceExcerpt("");
      setEvidenceUrl("");
      await loadDetail(selected.id, false);
      await refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add evidence");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddClaim(e: FormEvent) {
    e.preventDefault();
    if (!selected || !claimStatement.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addInvestigationClaim(selected.id, {
        statement: claimStatement.trim(),
        stance: "unknown",
        confidence_score: 0.4,
        rank: (selected.claims?.length ?? 0) + 1,
        evidence_ids: selected.evidence_items.map((ev) => ev.id).slice(0, 3),
      });
      setClaimStatement("");
      await loadDetail(selected.id, false);
      await refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add claim");
    } finally {
      setBusy(false);
    }
  }

  async function handleComplete() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const detail = await completeInvestigation(
        selected.id,
        selected.summary || `Investigation closed for ${selected.ticker}.`
      );
      setSelected(detail);
      await refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to complete");
    } finally {
      setBusy(false);
    }
  }

  async function handleRerun() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const detail = await runInvestigation(selected.id, {
        window_label: selected.window_label || "1d",
        skip_if_noise: false,
      });
      setSelected(detail);
      onSelectedChange(detail.id, detail.ticker);
      await refreshList();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to re-run investigation"
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleSearch(e: FormEvent) {
    e.preventDefault();
    const q = searchQuery.trim();
    if (!q) {
      setSearchHits([]);
      setSearchMode(null);
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const res = await searchInvestigations(q, 12);
      setSearchHits(res.results);
      setSearchMode(res.mode);
      if (res.results.length === 1) {
        await loadDetail(res.results[0].investigation_id);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not search investigations."
      );
    } finally {
      setSearching(false);
    }
  }

  async function handleScanHoldings() {
    setScanning(true);
    setScanNote(null);
    setError(null);
    try {
      const result = await sweepInvestigations({ dry_run: false });
      setScanNote(
        `Scan done: ${result.launched} launched, ${result.skipped_trigger} below trigger, ${result.skipped_cooldown} in cooldown` +
          (result.errors ? `, ${result.errors} errors` : "") +
          `.`
      );
      await refreshList();
      const launched = result.details.find(
        (d) => d.action === "launched" && typeof d.investigation_id === "number"
      );
      if (launched && typeof launched.investigation_id === "number") {
        await loadDetail(launched.investigation_id as number);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not scan holdings. Sign in and add watchlist tickers first."
      );
    } finally {
      setScanning(false);
    }
  }

  const pipelineBusy = busy || isPipelineLoading || isResearchLoading || scanning;

  return (
    <section className="card-surface p-5" aria-label="Evidence Ledger">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-300/80">
            Step 2 · Your cases
          </p>
          <h3 className="font-display mt-1 text-base font-semibold text-white">
            Evidence Ledger
          </h3>
          <p className="mt-1 max-w-xl text-sm text-slate-500">
            Each case leads with how much the ticker moved, then the leading
            cause, then reasons from each agent. Use the finder below only to
            reopen an old case — not to start a new one.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canScanHoldings && (
            <button
              type="button"
              onClick={() => void handleScanHoldings()}
              disabled={pipelineBusy}
              className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-100 transition hover:bg-amber-500/20 disabled:opacity-50"
              title="Evaluate watchlist moves with vol / residual gates"
            >
              {scanning ? "Scanning…" : "Scan my holdings"}
            </button>
          )}
          {selected && (
            <button
              type="button"
              onClick={() => void handleRerun()}
              disabled={pipelineBusy}
              className="rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-1.5 text-xs font-medium text-violet-200 transition hover:bg-violet-500/20 disabled:opacity-50"
            >
              {busy ? "Re-running…" : "Re-run investigation"}
            </button>
          )}
          {selected && onDeepResearch && (
            <button
              type="button"
              onClick={() => onDeepResearch(selected.ticker)}
              disabled={pipelineBusy}
              className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-200 transition hover:bg-cyan-500/20 disabled:opacity-50"
            >
              {isResearchLoading
                ? "Research running…"
                : "Gather with research squad"}
            </button>
          )}
          {selected &&
            selected.status !== "complete" &&
            selected.status !== "skipped_market_noise" && (
              <button
                type="button"
                onClick={() => void handleComplete()}
                disabled={pipelineBusy}
                className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-200 transition hover:bg-emerald-500/20 disabled:opacity-50"
              >
                Mark complete
              </button>
            )}
        </div>
      </div>

      {isPipelineLoading && (
        <div
          className="mt-3 rounded-xl border border-violet-500/25 bg-violet-500/[0.06] px-4 py-3"
          role="status"
        >
          <p className="text-sm font-medium text-violet-100">
            Agents are working the case…
          </p>
          <ol className="mt-2 grid gap-1.5 sm:grid-cols-2">
            {PIPELINE_AGENTS.map((agent) => (
              <li
                key={agent.name}
                className="flex items-start gap-2 text-xs text-slate-400"
              >
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-violet-400"
                  aria-hidden
                />
                <span>
                  <span className="font-medium text-slate-300">
                    {agent.name}
                  </span>
                  {" — "}
                  {agent.job}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {scanNote && (
        <p className="mt-3 text-sm text-amber-100/90" role="status">
          {scanNote}
        </p>
      )}

      <details className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2">
        <summary className="cursor-pointer list-none text-xs font-medium text-slate-400 marker:content-none [&::-webkit-details-marker]:hidden">
          <span className="inline-flex items-center gap-2">
            <span className="text-slate-500">Find a past case</span>
            <span className="font-normal text-slate-600">
              (search old investigations — not for new tickers)
            </span>
          </span>
        </summary>
        <form
          onSubmit={(e) => void handleSearch(e)}
          className="mt-3 flex flex-wrap gap-2"
          role="search"
          aria-label="Find past investigations"
        >
          <input
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              if (!e.target.value.trim()) {
                setSearchHits([]);
                setSearchMode(null);
              }
            }}
            placeholder='e.g. "export curb" or "guidance cut"'
            className="min-w-[14rem] flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-violet-500/40"
          />
          <button
            type="submit"
            disabled={searching || !searchQuery.trim()}
            className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-xs font-medium text-slate-200 transition hover:border-violet-500/30 disabled:opacity-40"
          >
            {searching ? "Searching…" : "Find cases"}
          </button>
        </form>
        {searchMode && (
          <p className="mt-2 text-[11px] text-slate-600">
            {searchHits.length} hit{searchHits.length === 1 ? "" : "s"} ·{" "}
            {searchMode}
            {searchHits.length === 0
              ? " — try wording from a claim or evidence snippet"
              : ""}
          </p>
        )}
        {searchHits.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {searchHits.map((hit) => (
              <li key={hit.investigation_id}>
                <button
                  type="button"
                  onClick={() => {
                    onSelectedChange(hit.investigation_id, hit.ticker);
                    void loadDetail(hit.investigation_id, false);
                  }}
                  className="w-full rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-left transition hover:border-violet-500/30 hover:bg-violet-500/5"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-display text-sm font-semibold text-white">
                      ${hit.ticker}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {hit.match_sources.join(" · ") || "match"}
                    </span>
                    <span className="text-[10px] text-slate-600">
                      score {hit.score.toFixed(1)}
                    </span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-slate-400">
                    {hit.snippet || hit.summary}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </details>

      {error && (
        <p className="mt-3 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,14rem)_1fr]">
        <div className="space-y-2">
          <p className="text-[11px] uppercase tracking-wide text-slate-600">
            Cases
          </p>
          {loading && <p className="text-sm text-slate-500">Loading…</p>}
          {!loading && items.length === 0 && (
            <p className="text-sm text-slate-500">
              No cases yet. Investigate a ticker above.
            </p>
          )}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                onSelectedChange(item.id, item.ticker);
                void loadDetail(item.id, false);
              }}
              className={`w-full rounded-xl border px-3 py-2.5 text-left transition ${
                selectedId === item.id
                  ? "border-violet-500/40 bg-violet-500/10"
                  : "border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12]"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-display text-sm font-semibold text-white">
                  ${item.ticker}
                </span>
                <span
                  className={`rounded-md border px-1.5 py-0.5 text-[10px] ${statusTone(item.status)}`}
                >
                  {item.status}
                </span>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                {item.claims_count} claims · {item.evidence_count} evidence
              </p>
            </button>
          ))}
        </div>

        <div className="min-w-0">
          {!selected && (
            <div className="rounded-xl border border-dashed border-white/[0.08] px-4 py-10 text-center">
              <p className="font-display text-sm font-semibold text-slate-300">
                No open case yet
              </p>
              <p className="mx-auto mt-2 max-w-sm text-sm text-slate-500">
                Use the ticker bar above (Step 1). You&apos;ll see how much it
                moved, what caused it, then each agent&apos;s reasons.
              </p>
            </div>
          )}

          {selected && (
            <div className="space-y-5">
              {(() => {
                const sortedClaims = [...selected.claims].sort(
                  (a, b) => (a.rank || 999) - (b.rank || 999)
                );
                const lead = sortedClaims[0];
                const resolvedMovePct = resolveMovePct(
                  selected.move_pct,
                  selected.summary
                );
                const headline = moveHeadline(
                  selected.ticker,
                  resolvedMovePct,
                  selected.window_label
                );
                return (
                  <>
              {/* 1. The move — always first */}
              <section className="rounded-xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] to-transparent px-4 py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    1 · The move
                  </p>
                  <span
                    className={`rounded-md border px-1.5 py-0.5 text-[10px] ${statusTone(selected.status)}`}
                  >
                    {selected.status}
                  </span>
                </div>
                <h4
                  className={`font-display mt-2 text-2xl font-bold tracking-tight sm:text-3xl ${headline.tone}`}
                >
                  {headline.pctLine}
                </h4>
                <p className="mt-2 text-sm text-slate-500">
                  Why did ${selected.ticker} {headline.verb} over{" "}
                  {humanWindow(selected.window_label)}?
                  {resolvedMovePct != null
                    ? ` Measured move: ${resolvedMovePct > 0 ? "+" : ""}${resolvedMovePct.toFixed(2)}%.`
                    : ""}
                </p>
              </section>

              {/* 2. What caused it */}
              <section className="rounded-xl border border-violet-500/25 bg-violet-500/[0.06] px-4 py-4">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-300/80">
                  2 · What caused it
                </p>
                {lead ? (
                  <>
                    <p className="mt-2 text-base font-medium leading-relaxed text-white">
                      {lead.statement}
                    </p>
                    <p className="mt-2 text-xs text-slate-500">
                      Leading explanation · weight{" "}
                      {(lead.confidence_score * 100).toFixed(0)}% · stance{" "}
                      {lead.stance || "n/a"}
                      {lead.evidence_links.length
                        ? ` · ${lead.evidence_links.length} receipt${lead.evidence_links.length === 1 ? "" : "s"}`
                        : ""}
                    </p>
                  </>
                ) : selected.summary ? (
                  <p className="mt-2 text-base leading-relaxed text-slate-200">
                    {selected.summary}
                  </p>
                ) : (
                  <p className="mt-2 text-sm text-slate-500">
                    Cause still forming — agents are ranking explanations.
                  </p>
                )}
                {selected.summary && lead && (
                  <p className="mt-3 border-t border-white/[0.06] pt-3 text-xs leading-relaxed text-slate-500">
                    {selected.summary}
                  </p>
                )}
              </section>

              {/* 3. Agents + their reasons */}
              <section>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  3 · Agents on the case
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  Each agent contributes a reason or check. Ranked by weight after
                  verification.
                </p>

                <ul className="mt-3 space-y-2">
                  <li className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-300/80">
                      Move detector
                    </p>
                    <p className="mt-1 text-sm text-slate-200">
                      {resolvedMovePct != null
                        ? `Price ${headline.verb} ${Math.abs(resolvedMovePct).toFixed(1)}% over ${humanWindow(selected.window_label)}.`
                        : `Could not resolve a clean % move for ${humanWindow(selected.window_label)} — hypotheses still use available evidence.`}
                    </p>
                  </li>

                  {sortedClaims.length === 0 ? (
                    <li className="rounded-lg border border-dashed border-white/[0.08] px-3 py-3 text-sm text-slate-500">
                      No agent hypotheses yet. Re-run the investigation after the
                      pipeline finishes.
                    </li>
                  ) : (
                    sortedClaims.map((claim, index) => {
                      const agentName =
                        CLAIM_AGENTS[index] ?? `Hypothesis agent ${index + 1}`;
                      const isLead = index === 0;
                      return (
                        <li
                          key={claim.id}
                          className={`rounded-lg border px-3 py-2.5 ${
                            isLead
                              ? "border-violet-500/35 bg-violet-500/[0.08]"
                              : "border-white/[0.06] bg-black/20"
                          }`}
                        >
                          <div className="flex flex-wrap items-center gap-2 text-[11px]">
                            <span className="font-semibold uppercase tracking-wide text-violet-200/90">
                              {agentName}
                            </span>
                            <span className="rounded border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-violet-200">
                              weight {(claim.confidence_score * 100).toFixed(0)}%
                            </span>
                            <span className="text-slate-600">
                              #{claim.rank || index + 1}
                            </span>
                            <span className="text-slate-600">{claim.stance}</span>
                            {isLead && (
                              <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-200">
                                leading cause
                              </span>
                            )}
                          </div>
                          <p className="mt-1.5 text-sm text-slate-200">
                            {claim.statement}
                          </p>
                          {claim.devil_advocate_notes && (
                            <p className="mt-1.5 text-xs text-amber-200/70">
                              Challenged: {claim.devil_advocate_notes}
                            </p>
                          )}
                        </li>
                      );
                    })
                  )}

                  {typeof selected.roster?.earnings?.earnings_summary ===
                    "string" && (
                    <li className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.05] px-3 py-2.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-200/80">
                        Earnings agent
                      </p>
                      <p className="mt-1 text-sm text-slate-300">
                        {selected.roster.earnings.earnings_summary as string}
                      </p>
                    </li>
                  )}
                  {typeof selected.roster?.macro?.macro_summary === "string" && (
                    <li className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.05] px-3 py-2.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-200/80">
                        Macro agent
                      </p>
                      <p className="mt-1 text-sm text-slate-300">
                        {selected.roster.macro.macro_summary as string}
                      </p>
                    </li>
                  )}
                  {typeof selected.roster?.memo?.one_liner === "string" && (
                    <li className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.05] px-3 py-2.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-200/80">
                        Memo agent
                      </p>
                      <p className="mt-1 text-sm text-slate-300">
                        {selected.roster.memo.one_liner as string}
                      </p>
                      {typeof selected.roster.memo.decision === "string" && (
                        <p className="mt-1 text-[11px] text-cyan-100/80">
                          Stance: {selected.roster.memo.decision as string}
                          {typeof selected.roster.memo.conviction === "string"
                            ? ` · ${selected.roster.memo.conviction as string} conviction`
                            : ""}
                        </p>
                      )}
                    </li>
                  )}

                  {(selected.verification_notes || selected.da_outcome) && (
                    <li className="rounded-lg border border-amber-500/25 bg-amber-500/[0.05] px-3 py-2.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-200/80">
                        Verifier &amp; Devil&apos;s Advocate
                      </p>
                      {selected.verification_notes && (
                        <p className="mt-1 text-xs text-slate-400">
                          {selected.verification_notes}
                        </p>
                      )}
                      {selected.da_outcome && (
                        <div className="mt-2 space-y-1.5">
                          <div className="flex flex-wrap items-center gap-2 text-[11px]">
                            <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-100">
                              Outcome: {selected.da_outcome.outcome}
                            </span>
                            {selected.da_outcome.reversal && (
                              <span className="rounded border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-red-200">
                                leading hypothesis demoted
                              </span>
                            )}
                            {selected.da_outcome.citation_coverage != null && (
                              <span className="text-slate-500">
                                citation coverage{" "}
                                {(
                                  selected.da_outcome.citation_coverage * 100
                                ).toFixed(0)}
                                %
                              </span>
                            )}
                          </div>
                          {selected.da_outcome.counterargument && (
                            <p className="text-sm text-amber-100/85">
                              {selected.da_outcome.counterargument}
                            </p>
                          )}
                        </div>
                      )}
                    </li>
                  )}
                </ul>

                <form onSubmit={handleAddClaim} className="mt-3 flex gap-2">
                  <input
                    value={claimStatement}
                    onChange={(e) => setClaimStatement(e.target.value)}
                    placeholder="Add another agent hypothesis…"
                    className="min-w-0 flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-violet-500/40"
                  />
                  <button
                    type="submit"
                    disabled={busy || !claimStatement.trim()}
                    className="rounded-lg border border-white/[0.1] px-3 py-2 text-xs text-slate-300 disabled:opacity-40"
                  >
                    Add
                  </button>
                </form>
              </section>
                  </>
                );
              })()}

              <section className="space-y-3" aria-label="Evidence report section">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-300/80">
                    4 · Evidence report
                  </p>
                  <p className="mt-1 text-sm text-slate-500">
                    Standalone dossier with source links — separate from the
                    agent narrative above.
                  </p>
                </div>

                <EvidenceReport
                  ticker={selected.ticker}
                  windowLabel={selected.window_label}
                  movePct={resolveMovePct(
                    selected.move_pct,
                    selected.summary
                  )}
                  items={selected.evidence_items}
                />

                <form
                  onSubmit={handleAddEvidence}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3 space-y-2"
                >
                  <p className="text-xs font-medium text-slate-400">
                    Add a receipt
                  </p>
                  <input
                    value={evidenceTitle}
                    onChange={(e) => setEvidenceTitle(e.target.value)}
                    placeholder="Evidence title"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/40"
                  />
                  <input
                    value={evidenceUrl}
                    onChange={(e) => setEvidenceUrl(e.target.value)}
                    placeholder="Source link (https://…)"
                    type="url"
                    inputMode="url"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/40"
                  />
                  <textarea
                    value={evidenceExcerpt}
                    onChange={(e) => setEvidenceExcerpt(e.target.value)}
                    placeholder="Excerpt / note"
                    rows={2}
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/40"
                  />
                  <button
                    type="submit"
                    disabled={busy}
                    className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-2 text-xs font-medium text-cyan-100 disabled:opacity-40"
                  >
                    Add evidence
                  </button>
                </form>
              </section>

              <section className="rounded-xl border border-violet-500/30 bg-gradient-to-br from-violet-500/[0.08] to-cyan-500/[0.04] px-4 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-300/80">
                      Wrap-up
                    </p>
                    <h5 className="font-display mt-1 text-base font-semibold text-white">
                      Smart Summarize
                    </h5>
                    <p className="mt-1 text-xs text-slate-500">
                      One short summary of the move, leading cause, and agent
                      takeaways.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSmartSummarize()}
                    disabled={summarizing || pipelineBusy}
                    className="rounded-xl bg-gradient-to-r from-violet-600 to-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {summarizing ? "Summarizing…" : "Smart Summarize"}
                  </button>
                </div>
                {smartSummary && (
                  <div className="mt-4 space-y-3 border-t border-white/[0.08] pt-4">
                    <p className="text-base font-medium leading-relaxed text-white">
                      {smartSummary.headline}
                    </p>
                    {smartSummary.bullets.length > 0 && (
                      <ul className="space-y-1.5">
                        {smartSummary.bullets.map((bullet, i) => (
                          <li
                            key={`${i}-${bullet.slice(0, 24)}`}
                            className="flex gap-2 text-sm text-slate-300"
                          >
                            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-violet-400" />
                            <span>{bullet}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2 text-sm text-slate-200">
                      <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                        Takeaway ·{" "}
                      </span>
                      {smartSummary.takeaway}
                    </p>
                    <p className="text-[10px] text-slate-600">
                      Source: {smartSummary.source === "llm" ? "AI" : "rules"}{" "}
                      · not financial advice
                    </p>
                  </div>
                )}
              </section>

              <InvestigationChatPanel
                investigationId={selected.id}
                ticker={selected.ticker}
              />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
