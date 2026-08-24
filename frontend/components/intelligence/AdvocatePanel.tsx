"use client";

import { ColumnHeader, NoSignal, Stamp } from "@/components/hud/primitives";
import { asRecord, asStringList } from "@/lib/investigationView";
import type { InvestigationDetail } from "@/types";

export default function AdvocatePanel({
  detail,
}: {
  detail: InvestigationDetail;
}) {
  const da = detail.da_outcome;
  const graphDa = asRecord(asRecord(detail.roster?.investigation_graph).da);
  const counter =
    da?.counterargument ||
    String(graphDa.strongest_counterargument || graphDa.counterargument || "");
  const contradicting = asStringList(
    graphDa.contradictory_evidence || da?.notes
  );
  const severity = da?.challenge_severity || String(graphDa.challenge_severity || "");
  const recommendation =
    da?.recommendation || String(graphDa.recommendation || da?.outcome || "");
  const weakness =
    asStringList(da?.questionable_assumptions || graphDa.questionable_assumptions)[0] ||
    asStringList(da?.additional_evidence_needed)[0] ||
    "";

  if (!counter && !severity && !recommendation) {
    return (
      <NoSignal
        title="Challenge not run yet"
        detail="The Devil's Advocate pass appears here after verification."
      />
    );
  }

  return (
    <section
      aria-label="Challenge the conclusion"
      className="border border-down bg-caution-50 p-4"
    >
      <ColumnHeader
        aside={severity ? <Stamp>{severity}</Stamp> : undefined}
        brief="A structured challenge against the leading explanation."
      >
        Challenge the Conclusion
      </ColumnHeader>
      {counter && (
        <p className="text-sm leading-relaxed text-osd">
          <span className="osd-label block">Strongest counterargument</span>
          {counter}
        </p>
      )}
      {contradicting.length > 0 && (
        <div className="mt-3">
          <p className="osd-label">Contradicting evidence</p>
          <ul className="mt-1 space-y-1 text-sm text-osd/90">
            {contradicting.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {weakness && (
        <p className="mt-3 text-sm text-osd/90">
          <span className="osd-label block">Potential weakness</span>
          {weakness}
        </p>
      )}
      {recommendation && (
        <p className="osd-label mt-4">Recommendation {recommendation}</p>
      )}
    </section>
  );
}
