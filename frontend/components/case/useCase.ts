"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchInvestigation } from "@/lib/api";
import { EXAMPLE_CASE, EXAMPLE_CASE_ID } from "@/lib/exampleCase";
import type { InvestigationDetail } from "@/types";

const SETTLED = new Set(["complete", "failed", "skipped_market_noise"]);

/**
 * Owns the active case so every column reads one source of truth. Polls only
 * while the pipeline is still working, then stops.
 */
export function useCase(investigationId: number | null) {
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(
    async (id: number, quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const next = await fetchInvestigation(id);
        setDetail(next);
        setError(null);
        return next;
      } catch {
        setError("Could not load this case. It may have been deleted.");
        return null;
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (investigationId == null) {
      setDetail(null);
      setError(null);
      return;
    }

    /* The bundled example is already in memory; it never needs fetching. */
    if (investigationId === EXAMPLE_CASE_ID) {
      setDetail(EXAMPLE_CASE);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    void load(investigationId).then((next) => {
      if (cancelled || !next || SETTLED.has(String(next.status))) return;
      timer.current = window.setInterval(async () => {
        const polled = await load(investigationId, true);
        if (polled && SETTLED.has(String(polled.status)) && timer.current) {
          window.clearInterval(timer.current);
          timer.current = null;
        }
      }, 4000);
    });

    return () => {
      cancelled = true;
      if (timer.current) {
        window.clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [investigationId, load]);

  return {
    detail,
    loading,
    error,
    reload: useCallback(
      () => (investigationId == null ? undefined : load(investigationId, true)),
      [investigationId, load]
    ),
  };
}

export function humanStance(stance: string): string {
  switch (stance) {
    case "supports_move":
      return "supports the move";
    case "contradicts":
    case "contradicts_move":
      return "argues against";
    case "neutral":
      return "context only";
    case "unknown":
    case "":
      return "direction unclear";
    default:
      return stance.replace(/_/g, " ");
  }
}

/* The band and the tallies already carry the move and the challenge outcome.
   Repeating them inside the summary made the same fact read three times, so
   those sentences are dropped here rather than at the source. */
export function tidySummary(summary: string, ticker?: string): string {
  /* Sentence ends only where a period follows a non-digit, so "2.86%" stays
     one number instead of becoming two sentences. */
  const sentences = summary
    .split(/(?<=[^\d][.!?])\s+(?=[A-Z(])/)
    .map((s) => s.trim())
    .filter(Boolean);

  const kept = sentences.filter((s, index) => {
    if (/devil'?s advocate/i.test(s)) return false;
    /* "Leading cause: …" restates the top-ranked claim shown right below. */
    if (/^leading cause:/i.test(s)) return false;
    if (
      index === 0 &&
      ticker &&
      new RegExp(`^${ticker}\\b[^.]*\\b(rose|fell)\\b`, "i").test(s)
    ) {
      return false;
    }
    return true;
  });

  return (kept.length > 0 ? kept : sentences).join(" ").trim();
}

export function humanSource(sourceType: string): string {
  switch (sourceType) {
    case "sec_filing":
      return "SEC filing";
    case "news":
      return "News";
    case "price":
      return "Price data";
    case "ir_page":
      return "Investor relations";
    case "earnings":
      return "Earnings";
    case "macro":
      return "Macro";
    default:
      return sourceType.replace(/_/g, " ");
  }
}
