"use client";

import { ExternalLink } from "lucide-react";
import {
  Bracketed,
  ColumnHeader,
  NoSignal,
  SourceTile,
  Stamp,
} from "@/components/hud/primitives";
import { humanSource } from "@/components/case/useCase";
import { AnimatedGroup } from "@/components/motion-primitives/animated-group";
import type { Claim, EvidenceItem } from "@/types";

type Props = {
  evidence: EvidenceItem[];
  claims: Claim[];
  busy?: boolean;
  hasCase: boolean;
};

/** Every receipt stays clickable, even when the collector returned no URL. */
function resolveUrl(item: EvidenceItem): string | null {
  if (item.source_url) return item.source_url;
  const found = item.excerpt?.match(/https?:\/\/[^\s)"']+/);
  if (found) return found[0];
  if (item.source_type === "sec_filing")
    return "https://www.sec.gov/edgar/search/";
  return null;
}

export default function ReceiptsColumn({
  evidence,
  claims,
  busy = false,
  hasCase,
}: Props) {
  const linkedIds = new Set(
    claims.flatMap((c) => c.evidence_links.map((l) => l.evidence_id))
  );

  return (
    <Bracketed
      as="section"
      large
      aria-label="Receipts collected for this case"
      className="flex h-full flex-col p-3 sm:p-4"
    >
      <ColumnHeader
        aside={evidence.length > 0 ? `${evidence.length} items` : undefined}
        brief="Every source the case actually read — filings, headlines, price data. Open any one to check it yourself."
      >
        Receipts
      </ColumnHeader>

      {!hasCase ? (
        <NoSignal
          title="No receipts yet"
          detail="Filings, headlines, and price data land here as the case collects them. Every claim above points back into this list."
        />
      ) : busy && evidence.length === 0 ? (
        <ul
          className="-mx-1 divide-y divide-hud/70"
          aria-label="Collecting evidence"
        >
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="flex items-center gap-3 px-1 py-3">
              <span className="h-7 w-9 shrink-0 bg-hud-deep" />
              <span className="osd-label">Collecting…</span>
            </li>
          ))}
        </ul>
      ) : evidence.length === 0 ? (
        <NoSignal
          title="Nothing collected"
          detail="No filings, headlines, or price evidence came back for this window."
        />
      ) : (
        <AnimatedGroup
          as="ol"
          preset="blur-slide"
          className="-mx-1 divide-y divide-hud/70"
        >
          {evidence.map((item, index) => {
            const url = resolveUrl(item);
            const orphan = !linkedIds.has(item.id);
            /* The title owns the full remaining width. Stamps and the link
               glyph sit on the meta line beneath it, because sharing the
               title's line squeezed long headlines to one word per line. */
            const body = (
              <>
                <span
                  data-figures
                  className="osd-label w-5 shrink-0 pt-px"
                  aria-hidden
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <SourceTile source={humanSource(item.source_type)} />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs leading-snug text-osd/90">
                    {item.title || item.excerpt.slice(0, 90) || "Untitled item"}
                  </span>
                  {orphan && (
                    <span className="mt-1.5 block">
                      <Stamp
                        tone="osd"
                        title="Collected but not linked to a claim"
                      >
                        Unlinked
                      </Stamp>
                    </span>
                  )}
                </span>
                {url && (
                  <ExternalLink
                    className="mt-px h-3.5 w-3.5 shrink-0 text-readout/70 transition-colors group-hover:text-caution"
                    strokeWidth={1.5}
                    aria-hidden
                  />
                )}
              </>
            );

            // Unlinked evidence is collected, not doubted, so it wears the
            // stamp without the zebra warning.
            return (
              <li key={item.id}>
                {url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-start gap-3 px-1 py-3 transition-colors hover:bg-hud-deep/70"
                  >
                    {body}
                  </a>
                ) : (
                  <div className="flex items-start gap-3 px-1 py-3">{body}</div>
                )}
              </li>
            );
          })}
        </AnimatedGroup>
      )}
    </Bracketed>
  );
}
