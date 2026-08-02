interface HeroSectionProps {
  compact?: boolean;
}

function QuantPilotMark({ className = "h-10 w-10" }: { className?: string }) {
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-500/20 via-indigo-500/10 to-cyan-500/10 shadow-glow ${className}`}
      aria-hidden
    >
      <svg
        viewBox="0 0 32 32"
        width="24"
        height="24"
        fill="none"
        className="h-6 w-6 text-violet-300"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M6 22 L10 14 L14 18 L18 10 L22 14 L26 8"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="26" cy="8" r="2" fill="currentColor" />
        <path
          d="M6 24 H26"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          opacity="0.4"
        />
      </svg>
    </div>
  );
}

const STEPS = [
  {
    n: "1",
    title: "Ask why it moved",
    body: "Type a ticker and choose 1 day, week, month, or year.",
  },
  {
    n: "2",
    title: "Read the case",
    body: "Evidence Ledger ranks claims with linked receipts.",
  },
  {
    n: "3",
    title: "Optional tools",
    body: "Search past cases, alerts, or a full research report.",
  },
];

export default function HeroSection({ compact = false }: HeroSectionProps) {
  if (compact) {
    return (
      <div className="gradient-border relative overflow-hidden px-5 py-4 sm:px-6 sm:py-5">
        <div
          className="pointer-events-none absolute -right-8 top-0 h-32 w-32 rounded-full bg-violet-500/10 blur-3xl"
          aria-hidden
        />
        <div className="relative z-10 flex items-center gap-4 sm:gap-5">
          <QuantPilotMark className="h-11 w-11 sm:h-12 sm:w-12" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-display text-lg font-bold tracking-tight text-white sm:text-xl">
                QuantPilot
              </span>
              <span className="vibe-pill px-2 py-0.5 text-violet-300/90">
                investigation engine
              </span>
            </div>
            <p className="mt-1.5 text-sm leading-snug text-slate-400 sm:text-base">
              Start above with a ticker. The ledger below holds your open case
              and past investigations.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <section className="gradient-border relative overflow-hidden px-5 py-8 sm:px-8 sm:py-10">
      <div
        className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-violet-500/15 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -bottom-16 -left-16 h-48 w-48 rounded-full bg-cyan-500/10 blur-3xl"
        aria-hidden
      />

      <div className="relative z-10 flex items-start gap-5">
        <QuantPilotMark className="hidden h-14 w-14 sm:flex" />
        <div className="min-w-0 flex-1">
          <p className="vibe-pill inline-block px-3 py-1 text-violet-300">
            Autonomous investigation engine
          </p>
          <h2 className="font-display mt-4 max-w-2xl text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl lg:text-4xl">
            Why did this move?{" "}
            <span className="gradient-text">Evidence, not vibes.</span>
          </h2>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-slate-400 sm:text-base">
            QuantPilot opens a case when a stock moves, gathers news and filings,
            ranks competing explanations, and stress-tests the lead before you
            decide anything.
          </p>

          <ol className="mt-6 grid gap-3 sm:grid-cols-3">
            {STEPS.map((step) => (
              <li
                key={step.n}
                className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-3"
              >
                <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-300/80">
                  {step.n}. {step.title}
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
                  {step.body}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
