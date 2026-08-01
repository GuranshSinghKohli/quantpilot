"use client";

import { useState } from "react";
import type { InvestmentMemo } from "@/types";

interface InvestmentMemoPanelProps {
  memo: InvestmentMemo;
}

function decisionStyle(decision: string): {
  bg: string;
  text: string;
} {
  const upper = decision.toUpperCase();
  if (upper === "BUY") {
    return { bg: "border-emerald-500/40 bg-emerald-500/10", text: "text-emerald-300" };
  }
  if (upper === "SELL") {
    return { bg: "border-red-500/40 bg-red-500/10", text: "text-red-300" };
  }
  if (upper === "WATCH") {
    return { bg: "border-cyan-500/40 bg-cyan-500/10", text: "text-cyan-300" };
  }
  return { bg: "border-amber-500/40 bg-amber-500/10", text: "text-amber-300" };
}

function memoToText(memo: InvestmentMemo): string {
  return [
    memo.memo_title,
    "",
    memo.one_liner,
    "",
    `Decision: ${memo.decision} | Conviction: ${memo.conviction} | Horizon: ${memo.time_horizon}`,
    "",
    "THESIS",
    memo.investment_thesis,
    "",
    "KEY NUMBERS",
    ...(memo.key_numbers || []).map((n) => `• ${n}`),
    "",
    "CATALYSTS",
    ...(memo.catalysts || []).map((c) => `• ${c}`),
    "",
    "RISKS",
    ...(memo.risks || []).map((r) => `• ${r}`),
    "",
    "BULL",
    memo.bull_case_summary,
    "",
    "BEAR",
    memo.bear_case_summary,
    "",
    "WHAT WOULD CHANGE MY MIND",
    ...(memo.what_would_change_my_mind || []).map((w) => `• ${w}`),
    "",
    memo.disclaimer,
  ].join("\n");
}

export default function InvestmentMemoPanel({ memo }: InvestmentMemoPanelProps) {
  const style = decisionStyle(memo.decision);
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(memoToText(memo));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <section
      className="card-surface overflow-hidden p-5 sm:p-6"
      aria-labelledby="investment-memo-title"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
            Investment memo
          </p>
          <h3
            id="investment-memo-title"
            className="font-display mt-1 text-lg font-semibold text-white sm:text-xl"
          >
            {memo.memo_title || `${memo.ticker} Investment Memo`}
          </h3>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-400">
            {memo.one_liner}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <span
            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide ${style.bg} ${style.text}`}
          >
            {memo.decision}
          </span>
          <span className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs text-slate-400">
            {memo.conviction} conviction
          </span>
          <span className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs text-slate-500">
            {memo.time_horizon}
          </span>
        </div>
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Thesis
          </h4>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            {memo.investment_thesis}
          </p>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Key numbers
          </h4>
          <ul className="mt-2 space-y-1.5">
            {(memo.key_numbers || []).map((item, i) => (
              <li key={i} className="text-sm text-slate-300">
                • {item}
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl border border-emerald-500/15 bg-emerald-500/[0.04] p-4">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-emerald-400/80">
            Bull case
          </h4>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            {memo.bull_case_summary}
          </p>
        </div>
        <div className="rounded-xl border border-red-500/15 bg-red-500/[0.04] p-4">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-red-400/80">
            Bear case
          </h4>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            {memo.bear_case_summary}
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Catalysts
          </h4>
          <ul className="mt-2 space-y-1">
            {(memo.catalysts || []).map((item, i) => (
              <li key={i} className="text-xs leading-relaxed text-slate-400">
                • {item}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Risks
          </h4>
          <ul className="mt-2 space-y-1">
            {(memo.risks || []).map((item, i) => (
              <li key={i} className="text-xs leading-relaxed text-slate-400">
                • {item}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            What would change my mind
          </h4>
          <ul className="mt-2 space-y-1">
            {(memo.what_would_change_my_mind || []).map((item, i) => (
              <li key={i} className="text-xs leading-relaxed text-slate-400">
                • {item}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-4">
        <p className="max-w-xl text-[10px] leading-relaxed text-slate-600">
          {memo.disclaimer}
        </p>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs text-slate-400 transition hover:border-cyan-500/30 hover:text-cyan-200"
        >
          {copied ? "Copied" : "Copy memo"}
        </button>
      </div>
    </section>
  );
}
