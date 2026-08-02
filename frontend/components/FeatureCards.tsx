const FEATURES = [
  {
    id: "investigate",
    icon: "⌕",
    title: "Evidence Ledger",
    body: "Investigations, claims, and linked evidence — the spine for answering “why did this move?” with receipts.",
    gradient: "from-violet-500/20 to-fuchsia-500/5",
    border: "hover:border-violet-500/30",
  },
  {
    id: "pipeline",
    icon: "🤖",
    title: "Multi-agent deep dive",
    body: "News, financials, SEC, earnings, macro, risk, bull vs bear, verification, report, and a shareable memo.",
    gradient: "from-amber-500/20 to-orange-500/5",
    border: "hover:border-amber-500/30",
  },
  {
    id: "portfolio",
    icon: "◈",
    title: "Portfolio OS",
    body: "Watchlist, broker position sync, daily briefings, and smart alerts on holdings you actually care about.",
    gradient: "from-cyan-500/20 to-blue-500/5",
    border: "hover:border-cyan-500/30",
  },
  {
    id: "chat",
    icon: "💬",
    title: "Grounded follow-ups",
    body: "Ask questions after the report. Answers stay tied to this run plus past research in vector memory.",
    gradient: "from-emerald-500/20 to-teal-500/5",
    border: "hover:border-emerald-500/30",
  },
];

interface FeatureCardsProps {
  className?: string;
}

export default function FeatureCards({ className = "" }: FeatureCardsProps) {
  return (
    <div className={`grid grid-cols-1 gap-3 sm:grid-cols-2 ${className}`}>
      {FEATURES.map((feature) => (
        <article
          key={feature.id}
          className={`card-surface card-surface-hover group p-5 transition ${feature.border}`}
        >
          <div
            className={`inline-flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br ${feature.gradient} text-lg`}
          >
            {feature.icon}
          </div>
          <h3 className="font-display mt-3 text-sm font-semibold text-white">
            {feature.title}
          </h3>
          <p className="mt-2 text-sm leading-relaxed text-slate-500 group-hover:text-slate-400">
            {feature.body}
          </p>
        </article>
      ))}
    </div>
  );
}
