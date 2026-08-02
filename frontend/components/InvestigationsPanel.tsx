"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  addInvestigationClaim,
  addInvestigationEvidence,
  ApiError,
  completeInvestigation,
  fetchInvestigation,
  fetchInvestigations,
  runInvestigation,
  searchInvestigations,
  sweepInvestigations,
} from "@/lib/api";
import type {
  InvestigationDetail,
  InvestigationSearchHit,
  InvestigationSummary,
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
  const [claimStatement, setClaimStatement] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<InvestigationSearchHit[]>([]);
  const [searchMode, setSearchMode] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

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
      return;
    }
    if (selected?.id === selectedId) return;
    void loadDetail(selectedId, false);
    void refreshList();
  }, [selectedId, selected?.id, loadDetail, refreshList]);

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
      });
      setEvidenceTitle("");
      setEvidenceExcerpt("");
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
            Results from Investigate land here: ranked claims, linked evidence,
            verification, and Devil&apos;s Advocate. Use the finder below only to
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
        <p className="mt-3 text-sm text-violet-200/90" role="status">
          Running investigation: detect → plan → collect → rank → verify →
          Devil&apos;s Advocate…
        </p>
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
                Use the ticker bar above (Step 1) to investigate. Your ranked
                claims and evidence will show up here.
              </p>
            </div>
          )}

          {selected && (
            <div className="space-y-5">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="font-display text-lg font-semibold text-white">
                    Why did ${selected.ticker} move?
                  </h4>
                  <span
                    className={`rounded-md border px-1.5 py-0.5 text-[10px] ${statusTone(selected.status)}`}
                  >
                    {selected.status}
                  </span>
                  <span className="text-xs text-slate-600">
                    {selected.trigger_reason === "scheduled"
                      ? "scheduled scan"
                      : selected.trigger_reason}
                    {selected.window_label
                      ? ` · window ${selected.window_label}`
                      : ""}
                    {selected.move_pct != null
                      ? ` · ${selected.move_pct > 0 ? "+" : ""}${selected.move_pct.toFixed(1)}%`
                      : ""}
                  </span>
                </div>
                {selected.summary && (
                  <p className="mt-2 text-sm text-slate-400">{selected.summary}</p>
                )}
              </div>

              {(selected.verification_notes || selected.da_outcome) && (
                <section className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] px-4 py-3">
                  <h5 className="text-xs font-semibold uppercase tracking-wide text-amber-200/80">
                    Verification layer
                  </h5>
                  {selected.verification_notes && (
                    <p className="mt-2 text-xs text-slate-400">
                      {selected.verification_notes}
                    </p>
                  )}
                  {selected.da_outcome && (
                    <div className="mt-2 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-100">
                          Devil&apos;s Advocate: {selected.da_outcome.outcome}
                        </span>
                        {selected.da_outcome.reversal && (
                          <span className="rounded border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-red-200">
                            leading hypothesis demoted
                          </span>
                        )}
                        {selected.da_outcome.citation_coverage != null && (
                          <span className="text-slate-500">
                            citation coverage{" "}
                            {(selected.da_outcome.citation_coverage * 100).toFixed(0)}%
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
                </section>
              )}

              {selected.roster &&
                (selected.roster.memo ||
                  selected.roster.earnings ||
                  selected.roster.macro) && (
                  <section className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-3 py-3 space-y-2">
                    <h5 className="text-xs font-semibold uppercase tracking-wide text-cyan-200/80">
                      Roster context
                    </h5>
                    {typeof selected.roster.memo?.one_liner === "string" && (
                      <p className="text-sm text-slate-200">
                        {selected.roster.memo.one_liner as string}
                      </p>
                    )}
                    <div className="grid gap-2 sm:grid-cols-2">
                      {typeof selected.roster.earnings?.earnings_summary ===
                        "string" && (
                        <p className="text-xs text-slate-400">
                          <span className="text-slate-500">Earnings · </span>
                          {selected.roster.earnings.earnings_summary as string}
                        </p>
                      )}
                      {typeof selected.roster.macro?.macro_summary ===
                        "string" && (
                        <p className="text-xs text-slate-400">
                          <span className="text-slate-500">Macro · </span>
                          {selected.roster.macro.macro_summary as string}
                        </p>
                      )}
                    </div>
                    {typeof selected.roster.memo?.decision === "string" && (
                      <p className="text-[11px] text-cyan-100/80">
                        Brief stance: {selected.roster.memo.decision as string}
                        {typeof selected.roster.memo.conviction === "string"
                          ? ` · ${selected.roster.memo.conviction as string} conviction`
                          : ""}
                      </p>
                    )}
                  </section>
                )}

              <section>
                <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Weighted hypotheses
                </h5>
                {selected.claims.length === 0 ? (
                  <p className="mt-2 text-sm text-slate-500">
                    No hypotheses yet. Investigate a ticker or re-run the
                    pipeline.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {selected.claims.map((claim, index) => (
                      <li
                        key={claim.id}
                        className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2"
                      >
                        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
                          <span>#{claim.rank || index + 1}</span>
                          <span className="rounded border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-violet-200">
                            weight {(claim.confidence_score * 100).toFixed(0)}%
                          </span>
                          <span>{claim.stance}</span>
                          <span>{claim.evidence_links.length} linked</span>
                        </div>
                        <p className="mt-1 text-sm text-slate-200">
                          {claim.statement}
                        </p>
                        {claim.devil_advocate_notes && (
                          <p className="mt-1 text-xs text-amber-200/70">
                            Devil&apos;s advocate: {claim.devil_advocate_notes}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <form onSubmit={handleAddClaim} className="mt-3 flex gap-2">
                  <input
                    value={claimStatement}
                    onChange={(e) => setClaimStatement(e.target.value)}
                    placeholder="Hypothesis / claim…"
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

              <section>
                <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Evidence
                </h5>
                {selected.evidence_items.length === 0 ? (
                  <p className="mt-2 text-sm text-slate-500">No evidence yet.</p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {selected.evidence_items.map((ev) => (
                      <li
                        key={ev.id}
                        className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2"
                      >
                        <p className="text-sm font-medium text-slate-200">
                          {ev.title || "Untitled"}
                        </p>
                        {ev.excerpt && (
                          <p className="mt-1 line-clamp-3 text-xs text-slate-500">
                            {ev.excerpt}
                          </p>
                        )}
                        <p className="mt-1 text-[11px] text-slate-600">
                          {ev.source_type} · {ev.retrieval_method}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
                <form onSubmit={handleAddEvidence} className="mt-3 space-y-2">
                  <input
                    value={evidenceTitle}
                    onChange={(e) => setEvidenceTitle(e.target.value)}
                    placeholder="Evidence title"
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-violet-500/40"
                  />
                  <textarea
                    value={evidenceExcerpt}
                    onChange={(e) => setEvidenceExcerpt(e.target.value)}
                    placeholder="Excerpt / note"
                    rows={2}
                    className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-violet-500/40"
                  />
                  <button
                    type="submit"
                    disabled={busy}
                    className="rounded-lg border border-white/[0.1] px-3 py-2 text-xs text-slate-300 disabled:opacity-40"
                  >
                    Add evidence
                  </button>
                </form>
              </section>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
