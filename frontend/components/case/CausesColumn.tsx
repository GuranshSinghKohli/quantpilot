"use client";

import { useState } from "react";
import CompanyMark from "@/components/hud/CompanyMark";
import { AnimatedGroup } from "@/components/motion-primitives/animated-group";
import {
  Bracketed,
  ColumnHeader,
  NoSignal,
  Stamp,
  WeightBar,
  ZebraEdge,
} from "@/components/hud/primitives";
import { humanStance, tidySummary } from "@/components/case/useCase";
import { cn } from "@/lib/utils";
import type { Claim, DevilsAdvocateOutcome } from "@/types";

type Props = {
  claims: Claim[];
  /** The plain-language verdict, read before the ranked detail. */
  summary?: string | null;
  daOutcome?: DevilsAdvocateOutcome | null;
  busy?: boolean;
  hasCase: boolean;
  /** Offered in the empty state, where a first move is the only useful action. */
  quickPicks?: string[];
  onQuickPick?: (ticker: string) => void;
  ticker?: string;
};

export default function CausesColumn({
  claims,
  summary,
  daOutcome,
  busy = false,
  hasCase,
  quickPicks = [],
  onQuickPick,
  ticker,
}: Props) {
  const ranked = [...claims].sort((a, b) => a.rank - b.rank);
  const lead = summary ? tidySummary(summary, ticker) : null;
  const [openId, setOpenId] = useState<number | null>(
    ranked.length > 0 ? ranked[0].id : null
  );

  return (
    <Bracketed
      as="section"
      large
      aria-label="What caused the move"
      className="flex h-full flex-col p-3 sm:p-4"
    >
      <ColumnHeader
        aside={ranked.length > 0 ? `${ranked.length} ranked` : undefined}
        brief="Competing explanations, in the order they survived challenge. Tap one to see its evidence."
      >
        What caused it
      </ColumnHeader>

      {lead && (
        <p className="mb-4 border-b border-hud pb-4 text-[0.8125rem] leading-relaxed text-osd/90">
          {lead}
        </p>
      )}

      {/* The demotion is announced once, in the headline band. Here it only
          needs to explain what it did to the order. */}
      {daOutcome?.reversal && (
        <p className="osd-label mb-4">Reordered after challenge</p>
      )}

      {!hasCase ? (
        <NoSignal
          title="Nothing framed yet"
          detail="Name a ticker in the bar above and QuantPilot ranks the competing explanations here, strongest first."
          action={
            quickPicks.length > 0 &&
            onQuickPick && (
              <div>
                <p className="osd-label mb-2.5">Start with</p>
                <div className="flex flex-wrap justify-center gap-1.5">
                  {quickPicks.slice(0, 4).map((sym) => (
                    <button
                      key={sym}
                      type="button"
                      onClick={() => onQuickPick(sym)}
                      className="flex items-center gap-2 border border-hud px-3 py-1.5 text-sm font-semibold text-osd hover:bg-osd hover:text-letterbox"
                    >
                      <CompanyMark ticker={sym} size="sm" />
                      {sym}
                    </button>
                  ))}
                </div>
              </div>
            )
          }
        />
      ) : busy && ranked.length === 0 ? (
        <ul className="divide-y divide-hud/70" aria-label="Ranking hypotheses">
          {[0, 1, 2].map((i) => (
            <li key={i} className="px-1 py-4">
              <span className="osd-label">Ranking…</span>
              <div className="mt-3 h-2 w-full bg-hud-deep" />
            </li>
          ))}
        </ul>
      ) : ranked.length === 0 ? (
        <NoSignal
          title="No claim cleared the bar"
          detail="The pipeline found no explanation it could support with evidence. That is a real answer, not an error."
        />
      ) : (
        <AnimatedGroup as="ol" preset="blur-slide" className="space-y-1">
          {ranked.map((claim, index) => {
            const lead = index === 0;
            const unverified = claim.evidence_links.length === 0;
            const demoted =
              Boolean(daOutcome?.reversal) && claim.rank !== 1 && index === 0;
            const open = openId === claim.id;

            /* Only the surviving lead is bracketed. The runners-up are
               hairline rows, so the answer reads first instead of competing
               with four identical boxes. */
            return (
              <li key={claim.id} className="relative">
                {unverified && <ZebraEdge />}
                <div
                  className={cn(
                    "relative",
                    lead
                      ? "border border-hud bg-hud-deep px-3 pb-3"
                      : "border-t border-hud/70 px-1 pb-3",
                    unverified ? "pt-4" : "pt-3"
                  )}
                >
                  <div className="flex gap-3">
                    <span
                      data-figures
                      className={cn(
                        "font-display shrink-0 text-lg font-extrabold leading-none",
                        lead ? "text-caution" : "text-readout"
                      )}
                      aria-hidden
                    >
                      {index + 1}
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <span className="osd-label">
                          {humanStance(claim.stance)}
                        </span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          {demoted && (
                            <Stamp title="Demoted by the Devil's Advocate pass">
                              Demoted
                            </Stamp>
                          )}
                          {unverified && (
                            <Stamp
                              tone="osd"
                              title="No evidence linked to this claim yet"
                            >
                              Unverified
                            </Stamp>
                          )}
                        </span>
                      </div>

                      <button
                        type="button"
                        onClick={() => setOpenId(open ? null : claim.id)}
                        aria-expanded={open}
                        className="mt-1.5 block w-full text-left"
                      >
                        <span
                          className={cn(
                            "text-[0.8125rem] leading-relaxed",
                            lead ? "text-osd" : "text-osd/85"
                          )}
                        >
                          {claim.statement}
                        </span>
                      </button>

                      <div className="mt-3 flex items-center gap-2.5">
                        <span className="osd-label shrink-0">Evidence</span>
                        <div className="min-w-0 flex-1">
                          <WeightBar
                            value={claim.confidence_score}
                            lead={lead}
                          />
                        </div>
                      </div>

                      <p className="osd-label mt-2">
                        {claim.evidence_links.length}{" "}
                        {claim.evidence_links.length === 1
                          ? "receipt"
                          : "receipts"}
                      </p>

                      {open && claim.devil_advocate_notes && (
                        <p className="mt-3 border-t border-hud pt-3 text-xs leading-relaxed text-readout">
                          <span className="text-caution">Challenged: </span>
                          {claim.devil_advocate_notes}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </AnimatedGroup>
      )}

      {daOutcome?.counterargument && (
        <div className="mt-4 border-t border-hud pt-4">
          <p className="osd-label mb-2">Devil&apos;s Advocate</p>
          <p className="text-xs leading-relaxed text-readout">
            {daOutcome.counterargument}
          </p>
        </div>
      )}
    </Bracketed>
  );
}
