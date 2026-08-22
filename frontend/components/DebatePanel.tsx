"use client";

import { TrendingDown, TrendingUp } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Bracketed, ColumnHeader, WeightBar } from "@/components/hud/primitives";
import type { DebateOutput } from "@/types";

interface DebatePanelProps {
  debate: DebateOutput;
}

function DebateCase({
  title,
  icon: Icon,
  thesis,
  points,
  confidence,
}: {
  title: string;
  icon: LucideIcon;
  thesis: string;
  points: string[];
  confidence: number;
}) {
  return (
    <Bracketed quiet className="p-4">
      <div className="mb-3 flex items-center justify-between gap-2 border-b border-hud pb-2.5">
        <h4 className="flex items-center gap-2 font-display text-[0.6875rem] font-bold uppercase tracking-osd text-osd">
          <Icon className="h-4 w-4 shrink-0 text-readout" strokeWidth={1.5} aria-hidden />
          {title}
        </h4>
      </div>

      <p className="text-xs leading-relaxed text-osd/85">{thesis}</p>

      {points.length > 0 && (
        <ul className="mt-4 space-y-2">
          {points.map((point, i) => (
            <li
              key={i}
              className="flex gap-2.5 text-xs leading-relaxed text-readout"
            >
              <span className="mt-1.5 h-1 w-1 shrink-0 bg-hud-line" aria-hidden />
              {point}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 border-t border-hud pt-3">
        <WeightBar value={confidence} />
      </div>
    </Bracketed>
  );
}

export default function DebatePanel({ debate }: DebatePanelProps) {
  return (
    <Bracketed as="section" large className="p-4 sm:p-5">
      <ColumnHeader aside="adversarial">Bull versus bear</ColumnHeader>
      <p className="mb-4 text-xs leading-relaxed text-readout">
        Two agents argue the thesis against each other before the report is
        written. Neither one gets the last word.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <DebateCase
          title="Bull case"
          icon={TrendingUp}
          thesis={debate.bull.thesis}
          points={debate.bull.key_points}
          confidence={debate.bull.confidence_score}
        />
        <DebateCase
          title="Bear case"
          icon={TrendingDown}
          thesis={debate.bear.thesis}
          points={debate.bear.key_points}
          confidence={debate.bear.confidence_score}
        />
      </div>
    </Bracketed>
  );
}
