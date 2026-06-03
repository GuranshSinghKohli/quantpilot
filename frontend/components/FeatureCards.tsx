const FEATURES = [
  {
    id: "market",
    icon: "📈",
    title: "Live market data",
    body: "Price, P/E, market cap — straight from Yahoo. Same vibes as a Bloomberg terminal, minus the $24k/year.",
    gradient: "from-emerald-500/20 to-cyan-500/5",
    border: "hover:border-emerald-500/30",
  },
  {
    id: "sec",
    icon: "🏛️",
    title: "SEC filing reads",
    body: "10-K, 10-Q, 8-K pulled from EDGAR. We scan for red flags so you don't have to read 200 pages.",
    gradient: "from-amber-500/20 to-orange-500/5",
    border: "hover:border-amber-500/30",
  },
  {
    id: "debate",
    icon: "⚔️",
    title: "Bull vs bear",
    body: "Two agents argue both sides before the final report drops. Real adversarial energy, not one-sided hype.",
    gradient: "from-violet-500/20 to-pink-500/5",
    border: "hover:border-violet-500/30",
  },
  {
    id: "chat",
    icon: "💬",
    title: "RAG chat",
    body: "Ask follow-ups after the report. Grounded in your analysis + past research stored in vector memory.",
    gradient: "from-cyan-500/20 to-blue-500/5",
    border: "hover:border-cyan-500/30",
  },
];

interface FeatureCardsProps {
  className?: string;
}

export default function FeatureCards({ className = "" }: FeatureCardsProps) {
  return (
    <div
      className={`grid grid-cols-1 gap-3 sm:grid-cols-2 ${className}`}
    >
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
