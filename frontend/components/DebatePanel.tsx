"use client";

import type { DebateOutput } from "@/types";

interface DebatePanelProps {
  debate: DebateOutput;
}

function DebateCard({
  title,
  emoji,
  accent,
  thesis,
  points,
  confidence,
}: {
  title: string;
  emoji: string;
  accent: string;
  thesis: string;
  points: string[];
  confidence: number;
}) {
  return (
    <div className={`rounded-xl border p-5 ${accent}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h4 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <span>{emoji}</span>
          {title}
        </h4>
        <span className="text-xs text-slate-500">
          {Math.round(confidence * 100)}% conf.
        </span>
      </div>
      <p className="text-sm leading-relaxed text-slate-300">{thesis}</p>
      {points.length > 0 && (
        <ul className="mt-4 space-y-2">
          {points.map((point, i) => (
            <li
              key={i}
              className="flex gap-2 text-xs leading-relaxed text-slate-400"
            >
              <span className="text-slate-600">•</span>
              {point}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function DebatePanel({ debate }: DebatePanelProps) {
  return (
    <section className="card-surface p-6">
      <h3 className="mb-1 text-sm font-semibold uppercase tracking-wider text-slate-400">
        Bull vs Bear Debate
      </h3>
      <p className="mb-5 text-xs text-slate-500">
        Adversarial agents stress-test the thesis before the final report.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <DebateCard
          title="Bull Case"
          emoji="🐂"
          accent="border-emerald-500/30 bg-emerald-500/5"
          thesis={debate.bull.thesis}
          points={debate.bull.key_points}
          confidence={debate.bull.confidence_score}
        />
        <DebateCard
          title="Bear Case"
          emoji="🐻"
          accent="border-red-500/30 bg-red-500/5"
          thesis={debate.bear.thesis}
          points={debate.bear.key_points}
          confidence={debate.bear.confidence_score}
        />
      </div>
    </section>
  );
}
