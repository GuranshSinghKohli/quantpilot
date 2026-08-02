const FEATURES = [
  {
    id: "investigate",
    icon: "⌕",
    title: "Why did it move?",
    body: "Pick a ticker and a window (day / week / month / 6 months / year). QuantPilot opens a case with ranked claims and receipts.",
    gradient: "from-violet-500/20 to-fuchsia-500/5",
    border: "hover:border-violet-500/30",
  },
  {
    id: "ledger",
    icon: "▤",
    title: "Evidence Ledger",
    body: "Your workspace for open and past cases — not a second ticker search. Find old investigations when you need them.",
    gradient: "from-cyan-500/20 to-blue-500/5",
    border: "hover:border-cyan-500/30",
  },
  {
    id: "pipeline",
    icon: "◈",
    title: "Optional deep research",
    body: "Full multi-agent report and memo when you want more than the investigation case.",
    gradient: "from-amber-500/20 to-orange-500/5",
    border: "hover:border-amber-500/30",
  },
  {
    id: "portfolio",
    icon: "★",
    title: "Portfolio tools",
    body: "Watchlist, briefings, and alerts sit under Portfolio tools — secondary to Investigate.",
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
