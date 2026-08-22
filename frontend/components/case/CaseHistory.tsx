"use client";

import { Search, Trash2 } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import CompanyMark from "@/components/hud/CompanyMark";
import {
  Bracketed,
  Caution,
  ColumnHeader,
  HudButton,
  NoSignal,
} from "@/components/hud/primitives";
import {
  ApiError,
  deleteInvestigation,
  fetchInvestigations,
  searchInvestigations,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { InvestigationSummary } from "@/types";

type Props = {
  selectedId: number | null;
  onSelect: (id: number, ticker: string) => void;
  refreshKey?: number;
};

function move(pct?: number | null): string {
  if (pct == null || !Number.isFinite(pct)) return "—";
  const sign = pct < 0 ? "\u2212" : "+";
  return `${sign}${Math.abs(pct).toFixed(2)}%`;
}

export default function CaseHistory({
  selectedId,
  onSelect,
  refreshKey = 0,
}: Props) {
  const [cases, setCases] = useState<InvestigationSummary[]>([]);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCases(await fetchInvestigations(20));
      setError(null);
    } catch {
      setError("Could not load past cases.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function runSearch(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) {
      setSearchNote(null);
      void load();
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const res = await searchInvestigations(q, 12);
      setCases(
        res.results.map((hit) => ({
          id: hit.investigation_id,
          ticker: hit.ticker,
          trigger_reason: hit.trigger_reason,
          status: hit.status,
          move_pct: hit.move_pct,
          window_label: "",
          summary: hit.snippet || hit.summary,
          created_at: hit.created_at,
          claims_count: hit.claims_count,
          evidence_count: hit.evidence_count,
        }))
      );
      setSearchNote(
        res.results.length === 0
          ? `Nothing in the ledger matches “${q}”.`
          : `${res.results.length} match${res.results.length === 1 ? "" : "es"} for “${q}”.`
      );
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Ledger search is unavailable."
      );
    } finally {
      setSearching(false);
    }
  }

  async function remove(id: number) {
    try {
      await deleteInvestigation(id);
      setCases((prev) => prev.filter((c) => c.id !== id));
    } catch {
      setError("Could not delete that case.");
    }
  }

  return (
    <Bracketed as="section" large aria-label="Past cases" className="p-3 sm:p-4">
      <ColumnHeader aside={cases.length > 0 ? `${cases.length}` : undefined}>
        Ledger
      </ColumnHeader>

      <form onSubmit={runSearch} className="mb-4 flex items-stretch gap-2">
        <label htmlFor="ledger-search" className="sr-only">
          Search past cases, claims, and evidence
        </label>
        <Bracketed
          snap
          className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2"
        >
          <Search className="h-4 w-4 shrink-0 text-readout" strokeWidth={1.5} aria-hidden />
          <input
            id="ledger-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search claims and evidence"
            className="min-w-0 flex-1 bg-transparent text-xs text-osd outline-none placeholder:text-readout"
          />
        </Bracketed>
        <HudButton type="submit" variant="ghost" loading={searching}>
          Search
        </HudButton>
      </form>

      {searchNote && (
        <p className="mb-3 text-xs text-readout">{searchNote}</p>
      )}
      {error && (
        <div className="mb-3">
          <Caution title="Ledger problem">{error}</Caution>
        </div>
      )}

      {loading ? (
        <p className="osd-label">Loading cases…</p>
      ) : cases.length === 0 ? (
        <NoSignal
          title="Ledger is empty"
          detail="Every case you frame is filed here with its claims and receipts."
        />
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {cases.map((c) => {
            const active = c.id === selectedId;
            /* Filed cases are tiles on a value step; only the open one is
               bracketed, so the ledger reads as a shelf with a marker. */
            return (
              <li key={c.id} className="relative">
                <div
                  className={cn(
                    "flex h-full items-start gap-3 px-3 py-2.5 transition-colors",
                    active
                      ? "bk bk-active bg-hud-deep"
                      : "bg-hud-deep/60 hover:bg-hud-deep"
                  )}
                >
                  <CompanyMark ticker={c.ticker} size="sm" className="mt-0.5" />
                  <button
                    type="button"
                    onClick={() => onSelect(c.id, c.ticker)}
                    className="min-w-0 flex-1 text-left"
                    aria-current={active ? "true" : undefined}
                  >
                    <span className="flex items-baseline justify-between gap-2">
                      <span
                        className={cn(
                          "font-display text-xs font-bold uppercase tracking-osd",
                          active ? "text-caution" : "text-osd"
                        )}
                      >
                        {c.ticker}
                      </span>
                      <span data-figures className="osd-readout text-readout">
                        {move(c.move_pct)}
                      </span>
                    </span>
                    <span className="osd-label mt-1.5 block">
                      {c.claims_count} claims · {c.evidence_count} receipts
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(c.id)}
                    aria-label={`Delete the ${c.ticker} case`}
                    className="shrink-0 p-1 text-readout transition-colors hover:text-caution"
                  >
                    <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Bracketed>
  );
}
