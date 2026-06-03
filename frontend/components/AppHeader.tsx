interface AppHeaderProps {
  apiReachable: boolean | null;
}

export default function AppHeader({ apiReachable }: AppHeaderProps) {
  return (
    <header className="sticky top-0 z-40 border-b border-white/[0.06] bg-[#07070d]/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <div className="min-w-0">
          <h1 className="font-display truncate text-lg font-bold tracking-tight sm:text-xl">
            <span className="text-white">Quant</span>
            <span className="gradient-text">Pilot</span>
          </h1>
          <p className="hidden truncate text-xs text-slate-500 sm:block">
            your AI research squad · no cap, just data
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {apiReachable !== null && (
            <span
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
                apiReachable
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                  : "border-amber-500/30 bg-amber-500/10 text-amber-400"
              }`}
              title={apiReachable ? "API connected" : "API offline"}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  apiReachable ? "animate-pulse bg-emerald-400" : "bg-amber-400"
                }`}
              />
              <span className="hidden sm:inline">
                {apiReachable ? "live" : "offline"}
              </span>
            </span>
          )}
          <div className="hidden items-center gap-1.5 md:flex">
            {["LangGraph", "MCP", "Chroma"].map((tag) => (
              <span key={tag} className="vibe-pill px-2 py-0.5 text-slate-500">
                {tag}
              </span>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}
