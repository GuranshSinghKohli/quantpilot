"use client";

import { useMemo, useState } from "react";
import type { EvidenceItem } from "@/types";

interface EvidenceReportProps {
  ticker: string;
  windowLabel?: string;
  movePct?: number | null;
  items: EvidenceItem[];
}

function isHttpUrl(value: string | undefined | null): value is string {
  if (!value) return false;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/** Pull first http(s) URL out of free text when source_url is empty. */
function extractUrlFromText(text: string): string | null {
  const match = text.match(/https?:\/\/[^\s)\]>'"]+/i);
  if (!match) return null;
  const cleaned = match[0].replace(/[.,;]+$/, "");
  return isHttpUrl(cleaned) ? cleaned : null;
}

function fallbackSourceUrl(ev: EvidenceItem, ticker: string): string | null {
  if (isHttpUrl(ev.source_url)) return ev.source_url;
  const fromText = extractUrlFromText(`${ev.title}\n${ev.excerpt}`);
  if (fromText) return fromText;

  const symbol = ticker.toUpperCase();
  switch ((ev.source_type || "").toLowerCase()) {
    case "news":
      return `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}/news`;
    case "filing":
    case "sec":
      return `https://www.sec.gov/edgar/search/#/entityName=${encodeURIComponent(symbol)}`;
    case "price":
    case "market":
      return `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}`;
    case "ir_page":
    case "browser":
      return null;
    default:
      return `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}`;
  }
}

function hostLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "source";
  }
}

function humanWindow(label?: string): string {
  switch ((label || "").toLowerCase()) {
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
    default:
      return label || "selected window";
  }
}

function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-white/[0.1] bg-white/[0.04] text-[10px] text-slate-300 transition ${
        open ? "rotate-180" : ""
      }`}
      aria-hidden
    >
      ▾
    </span>
  );
}

export default function EvidenceReport({
  ticker,
  windowLabel,
  movePct,
  items,
}: EvidenceReportProps) {
  const linked = useMemo(
    () =>
      items
        .map((ev, index) => {
          const url = fallbackSourceUrl(ev, ticker);
          return { ev, index, url, direct: isHttpUrl(ev.source_url) };
        })
        .filter((row): row is typeof row & { url: string } => Boolean(row.url)),
    [items, ticker]
  );

  const [linksOpen, setLinksOpen] = useState(false);
  const [bodyOpen, setBodyOpen] = useState(true);
  const [openItems, setOpenItems] = useState<Record<number, boolean>>(() =>
    items[0] ? { [items[0].id]: true } : {}
  );

  function toggleItem(id: number) {
    setOpenItems((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  function expandAllItems() {
    const next: Record<number, boolean> = {};
    for (const ev of items) next[ev.id] = true;
    setOpenItems(next);
    setBodyOpen(true);
  }

  function collapseAllItems() {
    setOpenItems({});
  }

  const moveBit =
    movePct == null
      ? null
      : `${movePct > 0 ? "+" : ""}${movePct.toFixed(1)}% over ${humanWindow(windowLabel)}`;

  return (
    <article
      className="overflow-hidden rounded-2xl border border-cyan-500/25 bg-[#0a0f14]"
      aria-label="Evidence report"
    >
      <header className="border-b border-cyan-500/20 bg-gradient-to-r from-cyan-500/10 via-transparent to-violet-500/5 px-5 py-4">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-300/80">
          Separate report · Evidence
        </p>
        <h4 className="font-display mt-1 text-xl font-bold text-white">
          ${ticker} evidence dossier
        </h4>
        <p className="mt-1 text-sm text-slate-400">
          Receipts and source links collected for this investigation
          {moveBit ? ` · move ${moveBit}` : ""}.
        </p>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
          <span className="rounded-md border border-cyan-500/25 bg-cyan-500/10 px-2 py-0.5 text-cyan-100">
            {items.length} item{items.length === 1 ? "" : "s"}
          </span>
          <span className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-slate-400">
            {linked.length} with source link{linked.length === 1 ? "" : "s"}
          </span>
          <span className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-slate-400">
            window {humanWindow(windowLabel)}
          </span>
        </div>
      </header>

      {linked.length > 0 && (
        <div className="border-b border-white/[0.06]">
          <button
            type="button"
            onClick={() => setLinksOpen((v) => !v)}
            aria-expanded={linksOpen}
            className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left transition hover:bg-white/[0.02]"
          >
            <span>
              <span className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Source links
              </span>
              <span className="mt-0.5 block text-sm text-slate-300">
                {linked.length} link{linked.length === 1 ? "" : "s"} · click to{" "}
                {linksOpen ? "hide" : "show"}
              </span>
            </span>
            <Chevron open={linksOpen} />
          </button>
          {linksOpen && (
            <ol className="space-y-1.5 px-5 pb-4">
              {linked.map(({ ev, index, url, direct }) => (
                <li
                  key={`link-${ev.id}`}
                  className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm"
                >
                  <span className="text-xs text-slate-600">[{index + 1}]</span>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-medium text-cyan-300 underline decoration-cyan-500/30 underline-offset-2 transition hover:text-cyan-200 hover:decoration-cyan-300/60"
                  >
                    {ev.title || hostLabel(url)}
                  </a>
                  <span className="text-[11px] text-slate-600">
                    {hostLabel(url)}
                    {!direct ? " · related hub" : ""}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      <div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-5 py-3">
          <button
            type="button"
            onClick={() => setBodyOpen((v) => !v)}
            aria-expanded={bodyOpen}
            className="flex min-w-0 flex-1 items-center justify-between gap-3 text-left"
          >
            <span>
              <span className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Evidence body
              </span>
              <span className="mt-0.5 block text-sm text-slate-300">
                {items.length} receipt{items.length === 1 ? "" : "s"} · dropdown
                each item
              </span>
            </span>
            <Chevron open={bodyOpen} />
          </button>
          {items.length > 0 && (
            <div className="flex shrink-0 gap-1.5">
              <button
                type="button"
                onClick={expandAllItems}
                className="rounded-lg border border-white/[0.1] bg-white/[0.03] px-2.5 py-1 text-[11px] font-medium text-slate-300 transition hover:border-cyan-500/30 hover:text-cyan-100"
              >
                Expand all
              </button>
              <button
                type="button"
                onClick={collapseAllItems}
                className="rounded-lg border border-white/[0.1] bg-white/[0.03] px-2.5 py-1 text-[11px] font-medium text-slate-300 transition hover:border-cyan-500/30 hover:text-cyan-100"
              >
                Collapse all
              </button>
            </div>
          )}
        </div>

        {bodyOpen && (
          <div className="px-5 py-4">
            {items.length === 0 ? (
              <p className="text-sm text-slate-500">
                No evidence collected yet. Re-run the investigation or add a
                receipt below.
              </p>
            ) : (
              <ul className="space-y-2">
                {items.map((ev, index) => {
                  const url = fallbackSourceUrl(ev, ticker);
                  const direct = isHttpUrl(ev.source_url);
                  const open = Boolean(openItems[ev.id]);
                  return (
                    <li
                      key={ev.id}
                      className="overflow-hidden rounded-xl border border-white/[0.07] bg-black/25"
                    >
                      <button
                        type="button"
                        onClick={() => toggleItem(ev.id)}
                        aria-expanded={open}
                        className="flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left transition hover:bg-white/[0.02]"
                      >
                        <span className="min-w-0">
                          <span className="block text-[11px] text-slate-600">
                            #{index + 1} · {ev.source_type || "source"} ·{" "}
                            {ev.retrieval_method || "unknown"}
                          </span>
                          <span className="mt-0.5 block truncate text-sm font-semibold text-slate-100">
                            {ev.title || "Untitled evidence"}
                          </span>
                        </span>
                        <Chevron open={open} />
                      </button>

                      {open && (
                        <div className="space-y-2 border-t border-white/[0.06] px-3.5 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            {url ? (
                              <a
                                href={url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-1 text-xs font-medium text-cyan-200 transition hover:bg-cyan-500/20"
                              >
                                Open source ↗
                              </a>
                            ) : (
                              <span className="rounded-lg border border-white/[0.08] px-2.5 py-1 text-xs text-slate-600">
                                No URL
                              </span>
                            )}
                          </div>
                          {ev.excerpt && (
                            <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-400">
                              {ev.excerpt}
                            </p>
                          )}
                          {url && (
                            <p className="break-all text-[11px]">
                              <span className="text-slate-600">Link: </span>
                              <a
                                href={url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-cyan-400/90 underline decoration-cyan-500/25 underline-offset-2 hover:text-cyan-300"
                              >
                                {url}
                              </a>
                              {!direct && (
                                <span className="ml-1 text-slate-600">
                                  (fallback hub)
                                </span>
                              )}
                            </p>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
