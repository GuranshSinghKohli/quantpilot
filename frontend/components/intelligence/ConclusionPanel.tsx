"use client";

import { ColumnHeader, NoSignal } from "@/components/hud/primitives";
import { asRecord, asStringList, supportLabel } from "@/lib/investigationView";
import { tidySummary } from "@/components/case/useCase";
import type { InvestigationDetail } from "@/types";

export default function ConclusionPanel({
  detail,
}: {
  detail: InvestigationDetail;
}) {
  const report = asRecord(detail.roster?.final_report);
  const leadClaim = [...(detail.claims || [])].sort((a, b) => a.rank - b.rank)[0];
  const primary =
    String(report.why_it_likely_happened || report.final_assessment || "") ||
    (detail.summary ? tidySummary(detail.summary, detail.ticker) : "") ||
    leadClaim?.statement ||
    "";
  const alternative =
    String(report.strongest_alternative || "") ||
    [...(detail.claims || [])].sort((a, b) => a.rank - b.rank)[1]?.statement ||
    "";
  const limitation =
    asStringList(report.evidence_gaps)[0] ||
    leadClaim?.devil_advocate_notes ||
    "";
  const strength = String(
    report.conclusion_strength ||
      asRecord(detail.roster?.verification_summary).conclusion_strength ||
      (leadClaim ? supportLabel(leadClaim) : "")
  );

  if (!primary) {
    return (
      <NoSignal
        title="No final assessment yet"
        detail="The conclusion is written after synthesis. Incomplete cases do not invent one."
      />
    );
  }

  return (
    <section aria-label="Final conclusion" className="border border-caution p-4">
      <ColumnHeader aside={strength || undefined}>Final conclusion</ColumnHeader>
      <p className="osd-label">Primary explanation</p>
      <p className="mt-1 text-sm leading-relaxed text-osd">{primary}</p>
      {strength && (
        <p className="mt-3 text-sm text-osd">
          <span className="osd-label block">Support</span>
          {strength}
        </p>
      )}
      {alternative && (
        <p className="mt-3 text-sm text-osd/90">
          <span className="osd-label block">Alternative</span>
          {alternative}
        </p>
      )}
      {limitation && (
        <p className="mt-3 text-sm text-readout">
          <span className="osd-label block">Limitation</span>
          {limitation}
        </p>
      )}
    </section>
  );
}
