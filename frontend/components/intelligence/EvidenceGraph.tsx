"use client";

import { formatMove } from "@/lib/investigationView";
import type { Claim, EvidenceItem } from "@/types";

export default function EvidenceGraph({
  ticker,
  movePct,
  claims,
  evidence,
}: {
  ticker: string;
  movePct?: number | null;
  claims: Claim[];
  evidence: EvidenceItem[];
}) {
  const lead = [...claims].sort((a, b) => a.rank - b.rank)[0];
  const links = lead?.evidence_links || [];
  if (!lead) {
    return (
      <p className="text-sm text-readout">
        No hypothesis–evidence links to draw yet.
      </p>
    );
  }
  return (
    <div className="osd-readout overflow-x-auto text-osd">
      <div className="min-w-[20rem] text-center">
        <div className="mx-auto inline-block border border-hud px-3 py-2">
          Event
          <div className="text-osd">
            {ticker} {formatMove(movePct)}
          </div>
        </div>
        <div className="my-1 text-readout">│</div>
        <div className="mx-auto inline-block border border-caution px-3 py-2">
          Hypothesis
          <div className="max-w-xs truncate text-osd">{lead.statement}</div>
        </div>
        <div className="my-1 text-readout">│</div>
        <div className="flex justify-center gap-3">
          {links.length > 0
            ? links.slice(0, 4).map((link) => {
                const item =
                  link.evidence ||
                  evidence.find((e) => e.id === link.evidence_id);
                return (
                  <div
                    key={link.id}
                    className="max-w-[10rem] border border-hud px-2 py-2 text-left"
                  >
                    <div className="osd-label text-readout">
                      {link.relation.toUpperCase()}
                    </div>
                    <div className="truncate">
                      {item?.title || `Evidence #${link.evidence_id}`}
                    </div>
                  </div>
                );
              })
            : evidence.slice(0, 3).map((item) => (
                <div
                  key={item.id}
                  className="max-w-[10rem] border border-hud px-2 py-2 text-left"
                >
                  <div className="osd-label text-readout">CONTEXT</div>
                  <div className="truncate">{item.title || item.source_type}</div>
                </div>
              ))}
        </div>
      </div>
    </div>
  );
}
