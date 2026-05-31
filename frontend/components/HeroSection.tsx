interface HeroSectionProps {
  compact?: boolean;
}

function QuantPilotMark({ className = "h-10 w-10" }: { className?: string }) {
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-xl border border-accent/25 bg-gradient-to-br from-accent/20 to-indigo-600/10 shadow-[0_0_24px_rgba(59,130,246,0.15)] ${className}`}
      aria-hidden
    >
      <svg
        viewBox="0 0 32 32"
        fill="none"
        className="h-6 w-6 text-accent"
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

export default function HeroSection({ compact = false }: HeroSectionProps) {
  if (compact) {
    return (
      <div className="relative overflow-hidden rounded-2xl border border-line/80 bg-gradient-to-r from-[#12121a] via-[#111827] to-[#0f172a] px-5 py-4 sm:px-6 sm:py-5">
        <div
          className="pointer-events-none absolute -right-8 top-0 h-32 w-32 rounded-full bg-accent/5 blur-2xl"
          aria-hidden
        />
        <div className="relative flex items-center gap-4 sm:gap-5">
          <QuantPilotMark className="h-11 w-11 sm:h-12 sm:w-12" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-lg font-bold tracking-tight text-white sm:text-xl">
                QuantPilot
              </span>
              <span className="hidden h-4 w-px bg-line sm:block" aria-hidden />
              <span className="text-xs font-medium uppercase tracking-wider text-accent/90">
                Research copilot
              </span>
            </div>
            <p className="mt-1.5 text-base leading-snug text-slate-300 sm:text-[1.05rem] sm:leading-relaxed">
              AI equity research from{" "}
              <span className="font-medium text-slate-200">live market data</span>,{" "}
              <span className="font-medium text-amber-200/90">SEC filings</span>, and{" "}
              <span className="font-medium text-slate-200">news</span>.
            </p>
            <div className="mt-2.5 flex flex-wrap gap-2">
              {["Yahoo Finance", "SEC EDGAR", "5 AI agents"].map((label) => (
                <span
                  key={label}
                  className="rounded-md border border-line/80 bg-[#0a0a0f]/50 px-2 py-0.5 text-[11px] text-slate-500"
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <section className="relative overflow-hidden rounded-2xl border border-line bg-gradient-to-br from-[#12121a] via-[#0f1419] to-[#0a1628] px-5 py-8 sm:px-8 sm:py-10">
      <div
        className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-accent/10 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -bottom-16 -left-16 h-48 w-48 rounded-full bg-indigo-500/10 blur-3xl"
        aria-hidden
      />

      <div className="relative flex items-start gap-5">
        <QuantPilotMark className="h-14 w-14 hidden sm:flex" />
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-accent">
            AI Quant Research Copilot
          </p>
          <h2 className="mt-3 max-w-2xl text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl lg:text-4xl">
            Turn any ticker into a{" "}
            <span className="bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
              research-grade equity report
            </span>{" "}
            in under a minute
          </h2>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-slate-400 sm:text-base">
            QuantPilot runs five specialized AI agents on real data — price &amp;
            fundamentals, headlines, and SEC disclosures — then synthesizes risk and a
            full written report with confidence scores and cited sources.
          </p>

          <ul className="mt-6 flex flex-wrap gap-2 sm:gap-3">
            {[
              "Multi-agent LangGraph",
              "Live Yahoo Finance",
              "SEC EDGAR filings",
              "Risk + recommendation",
            ].map((tag) => (
              <li
                key={tag}
                className="rounded-full border border-line bg-[#0a0a0f]/60 px-3 py-1 text-xs text-slate-400"
              >
                {tag}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
