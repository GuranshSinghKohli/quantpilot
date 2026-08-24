"use client";

import { ColumnHeader, NoSignal } from "@/components/hud/primitives";
import { asRecord, asStringList } from "@/lib/investigationView";
import type { InvestigationDetail } from "@/types";

export default function VerificationPanel({
  detail,
}: {
  detail: InvestigationDetail;
}) {
  const summary = asRecord(detail.roster?.verification_summary);
  const report = asRecord(asRecord(detail.roster?.final_report).verification);
  const src = Object.keys(summary).length > 0 ? summary : report;
  const gaps = asStringList(detail.roster?.evidence_gaps);
  const claimsReviewed = Number(src.claims_reviewed || 0);
  const hasData =
    claimsReviewed > 0 ||
    Number(src.supported || 0) > 0 ||
    gaps.length > 0 ||
    Boolean(detail.verification_notes);

  if (!hasData) {
    return (
      <NoSignal
        title="Verification not available yet"
        detail={
          ["planning", "collecting"].includes(String(detail.status))
            ? "Investigation is still running. Some evidence is not available yet."
            : "No verification summary was stored for this case."
        }
      />
    );
  }

  return (
    <section aria-label="Verification">
      <ColumnHeader brief="Claim-level review against stored evidence. Not a confidence percentage.">
        Verification
      </ColumnHeader>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Claims reviewed" value={String(claimsReviewed)} />
        <Stat label="Supported" value={String(src.supported ?? 0)} />
        <Stat label="Partially supported" value={String(src.partially_supported ?? 0)} />
        <Stat label="Unsupported" value={String(src.unsupported ?? 0)} />
        <Stat label="Contradicted" value={String(src.contradicted ?? 0)} />
        <Stat
          label="Primary-source coverage"
          value={String(src.primary_source_coverage || "—")}
        />
        <Stat label="Evidence gaps" value={String(gaps.length)} />
      </dl>
      {gaps.length > 0 && (
        <ul className="mt-4 space-y-1 text-sm text-osd/90">
          {gaps.map((gap) => (
            <li key={gap}>{gap}</li>
          ))}
        </ul>
      )}
      {detail.verification_notes && (
        <p className="mt-3 text-sm text-readout">{detail.verification_notes}</p>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-hud px-3 py-2">
      <dt className="osd-label">{label}</dt>
      <dd data-figures className="mt-1 font-mono text-sm text-osd">
        {value}
      </dd>
    </div>
  );
}
