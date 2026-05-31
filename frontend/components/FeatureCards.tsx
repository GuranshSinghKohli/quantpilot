const FEATURES = [
  {
    id: "market",
    icon: "📈",
    title: "Market & fundamentals",
    body: "Live price, P/E, market cap, and 52-week range from Yahoo Finance — the same inputs analysts use for a quick valuation snapshot.",
    accent: "border-t-emerald-500/80",
  },
  {
    id: "sec",
    icon: "🏛️",
    title: "SEC filings",
    body: "SEC filings are official documents U.S. public companies file with the Securities and Exchange Commission — e.g. annual 10-K, quarterly 10-Q, and event 8-K reports. We pull recent filings from EDGAR to flag disclosure and regulatory risk signals.",
    accent: "border-t-amber-500/80",
  },
  {
    id: "ai",
    icon: "🤖",
    title: "AI research report",
    body: "Agents synthesize news sentiment, valuation, and filing risks into an executive summary, structured sections, and a BUY / HOLD / SELL-style view — with facts separated from AI-generated insights.",
    accent: "border-t-blue-500/80",
  },
];

interface FeatureCardsProps {
  className?: string;
}

export default function FeatureCards({ className = "" }: FeatureCardsProps) {
  return (
    <div
      className={`grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 ${className}`}
    >
      {FEATURES.map((feature) => (
        <article
          key={feature.id}
          className={`card-surface border-t-2 ${feature.accent} p-5 transition hover:border-[#2a2a3e]`}
        >
          <span className="text-2xl" role="img" aria-hidden>
            {feature.icon}
          </span>
          <h3 className="mt-3 text-sm font-semibold text-white">{feature.title}</h3>
          <p className="mt-2 text-sm leading-relaxed text-slate-400">{feature.body}</p>
        </article>
      ))}
    </div>
  );
}
