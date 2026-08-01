"use client";

import type { AnalysisResponse } from "@/types";

interface ResearchExtrasPanelProps {
  analysis: AnalysisResponse;
}

export default function ResearchExtrasPanel({
  analysis,
}: ResearchExtrasPanelProps) {
  const earnings = analysis.earnings_output;
  const macro = analysis.macro_output;
  const verification = analysis.verification_output;

  if (!earnings && !macro && !verification) return null;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {earnings && (
        <section className="card-surface p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Earnings
          </h3>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            {earnings.earnings_summary}
          </p>
          <p className="mt-3 text-xs text-slate-500">
            Tone: {earnings.tone}
            {earnings.next_catalyst
              ? ` · Next: ${earnings.next_catalyst}`
              : ""}
          </p>
          {earnings.key_points?.length > 0 && (
            <ul className="mt-3 space-y-1">
              {earnings.key_points.slice(0, 4).map((p, i) => (
                <li key={i} className="text-xs text-slate-500">
                  • {p}
                </li>
              ))}
            </ul>
          )}
          {(earnings.sources?.length || analysis.ir_materials?.sources?.length) ? (
            <p className="mt-3 text-xs text-cyan-500/80">
              IR:{" "}
              {(earnings.sources || analysis.ir_materials?.sources || [])
                .slice(0, 1)
                .join("")}
            </p>
          ) : null}
        </section>
      )}

      {macro && (
        <section className="card-surface p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Macro
          </h3>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            {macro.macro_summary}
          </p>
          <p className="mt-3 text-xs text-slate-500">
            Relevance: {macro.relevance}
          </p>
          {macro.themes?.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {macro.themes.slice(0, 6).map((t) => (
                <li
                  key={t}
                  className="rounded border border-[#1e1e2e] bg-[#0a0a0f]/50 px-2 py-0.5 text-xs text-slate-400"
                >
                  {t}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {verification && (
        <section className="card-surface p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Verification
          </h3>
          <p className="mt-2 text-sm text-slate-300">
            Groundedness{" "}
            <span className="font-medium text-slate-200">
              {Math.round(verification.groundedness_score * 100)}%
            </span>
          </p>
          {verification.verified_claims?.length > 0 && (
            <ul className="mt-3 space-y-1">
              {verification.verified_claims.slice(0, 3).map((c, i) => (
                <li key={i} className="text-xs text-emerald-500/80">
                  ✓ {c}
                </li>
              ))}
            </ul>
          )}
          {verification.unsupported_claims?.length > 0 && (
            <ul className="mt-2 space-y-1">
              {verification.unsupported_claims.slice(0, 3).map((c, i) => (
                <li key={i} className="text-xs text-amber-400/80">
                  ! {c}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
