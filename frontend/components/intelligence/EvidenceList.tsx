"use client";

import { ExternalLink } from "lucide-react";
import { ColumnHeader, NoSignal, Stamp } from "@/components/hud/primitives";
import { formatWhen, qualityBadge } from "@/lib/investigationView";
import { humanSource } from "@/components/case/useCase";
import type { Claim, EvidenceItem } from "@/types";

export default function EvidenceList({
  evidence,
  claims,
  incomplete,
}: {
  evidence: EvidenceItem[];
  claims: Claim[];
  incomplete?: boolean;
}) {
  const roleById = new Map<number, string[]>();
  for (const claim of claims) {
    for (const link of claim.evidence_links || []) {
      const list = roleById.get(link.evidence_id) || [];
      list.push(`${link.relation} · ${claim.statement.slice(0, 72)}`);
      roleById.set(link.evidence_id, list);
    }
  }

  return (
    <section aria-label="Evidence">
      <ColumnHeader
        aside={evidence.length > 0 ? `${evidence.length} items` : undefined}
        brief="Stored receipts only. Missing URLs stay missing."
      >
        Evidence
      </ColumnHeader>
      {incomplete && evidence.length === 0 && (
        <p className="mb-3 text-sm text-readout">
          Investigation is still running. Some evidence is not available yet.
        </p>
      )}
      {evidence.length === 0 ? (
        <NoSignal
          title="No evidence stored"
          detail="Primary sources, filings, and headlines land here when the collector persists them."
        />
      ) : (
        <ol className="space-y-3">
          {evidence.map((item) => {
            const roles = roleById.get(item.id) || [];
            return (
              <li key={item.id} className="border border-hud p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Stamp tone="osd">{qualityBadge(item)}</Stamp>
                  <span className="osd-label">{humanSource(item.source_type)}</span>
                </div>
                <p className="mt-2 text-sm text-osd">
                  {item.title || item.excerpt.slice(0, 120) || "Untitled item"}
                </p>
                <dl className="mt-2 grid gap-1 text-sm text-readout sm:grid-cols-2">
                  <div>
                    <dt className="osd-label">Source</dt>
                    <dd>{item.source_name || item.source_type}</dd>
                  </div>
                  <div>
                    <dt className="osd-label">Published</dt>
                    <dd>{formatWhen(item.published_at)}</dd>
                  </div>
                  <div>
                    <dt className="osd-label">Retrieved</dt>
                    <dd>{formatWhen(item.retrieved_at || item.created_at)}</dd>
                  </div>
                  {item.relevance && (
                    <div>
                      <dt className="osd-label">Relevance</dt>
                      <dd>{item.relevance}</dd>
                    </div>
                  )}
                </dl>
                {roles.length > 0 && (
                  <p className="mt-2 text-sm text-osd/90">{roles[0]}</p>
                )}
                {item.source_url && (
                  <a
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-3 inline-flex items-center gap-1 font-mono text-sm uppercase tracking-osd text-caution hover:underline"
                  >
                    View source
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  </a>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
