"use client";

import { useState } from "react";
import { ColumnHeader, NoSignal, Stamp } from "@/components/hud/primitives";
import { supportLabel } from "@/lib/investigationView";
import { cn } from "@/lib/utils";
import type { Claim, EvidenceItem } from "@/types";

export default function HypothesisPanel({
  claims,
  evidence,
}: {
  claims: Claim[];
  evidence: EvidenceItem[];
}) {
  const ranked = [...claims].sort((a, b) => a.rank - b.rank);
  const [openId, setOpenId] = useState<number | null>(ranked[0]?.id ?? null);

  if (ranked.length === 0) {
    return (
      <NoSignal
        title="No hypotheses yet"
        detail="Competing explanations appear here once the planner ranks them."
      />
    );
  }

  return (
    <section aria-label="Hypotheses">
      <ColumnHeader
        aside={`${ranked.length} ranked`}
        brief="QuantPilot considered more than one explanation. The lead survived ranking and challenge."
      >
        Hypotheses
      </ColumnHeader>
      <ol className="space-y-3">
        {ranked.map((claim, index) => {
          const supports = (claim.evidence_links || []).filter(
            (l) => l.relation === "supports"
          );
          const contradicts = (claim.evidence_links || []).filter(
            (l) => l.relation === "contradicts"
          );
          const context = (claim.evidence_links || []).filter(
            (l) => l.relation === "context"
          );
          const open = openId === claim.id;
          const lead = index === 0;
          return (
            <li
              key={claim.id}
              className={cn(
                "border border-hud p-3",
                lead && "bg-hud-deep"
              )}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="osd-label">
                  {lead ? "Leading explanation" : index === 1 ? "Alternative" : "Weaker"}
                </span>
                <Stamp tone={lead ? "caution" : "osd"}>
                  Support {supportLabel(claim)}
                </Stamp>
              </div>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpenId(open ? null : claim.id)}
                className="mt-2 block w-full text-left text-sm leading-relaxed text-osd"
              >
                {claim.statement}
              </button>
              <p className="osd-label mt-2">
                {supports.length} supporting · {contradicts.length} contradicting
              </p>
              {open && (
                <div className="mt-3 space-y-3 border-t border-hud pt-3 text-sm text-osd/90">
                  <EvidenceList
                    label="Why supported"
                    links={supports}
                    evidence={evidence}
                  />
                  <EvidenceList
                    label="What contradicts"
                    links={contradicts}
                    evidence={evidence}
                  />
                  {context.length > 0 && (
                    <EvidenceList
                      label="Context"
                      links={context}
                      evidence={evidence}
                    />
                  )}
                  {claim.devil_advocate_notes && (
                    <p>
                      <span className="osd-label block">Limitation</span>
                      {claim.devil_advocate_notes}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function EvidenceList({
  label,
  links,
  evidence,
}: {
  label: string;
  links: Claim["evidence_links"];
  evidence: EvidenceItem[];
}) {
  if (links.length === 0) {
    return (
      <p>
        <span className="osd-label block">{label}</span>
        None stored.
      </p>
    );
  }
  return (
    <div>
      <span className="osd-label block">{label}</span>
      <ul className="mt-1 space-y-1">
        {links.map((link) => {
          const item =
            link.evidence || evidence.find((e) => e.id === link.evidence_id);
          return (
            <li key={link.id}>
              {item?.title || `Evidence #${link.evidence_id}`}
              {link.note ? ` — ${link.note}` : ""}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
