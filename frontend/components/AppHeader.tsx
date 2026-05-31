interface AppHeaderProps {
  apiReachable: boolean | null;
}

export default function AppHeader({ apiReachable }: AppHeaderProps) {
  return (
    <header className="sticky top-0 z-40 border-b border-[#1e1e2e] bg-[#0a0a0f]/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-bold tracking-tight text-white sm:text-xl">
            Quant<span className="text-accent">Pilot</span>
          </h1>
          <p className="hidden truncate text-xs text-slate-500 sm:block">
            Institutional-style research, powered by AI agents
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {apiReachable !== null && (
            <span
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
                apiReachable
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                  : "border-amber-500/30 bg-amber-500/10 text-amber-400"
              }`}
              title={apiReachable ? "API connected" : "API offline"}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  apiReachable ? "bg-emerald-400" : "bg-amber-400"
                }`}
              />
              <span className="hidden sm:inline">
                {apiReachable ? "API online" : "API offline"}
              </span>
            </span>
          )}
          <div className="hidden items-center gap-2 text-[10px] text-slate-600 md:flex">
            <span className="rounded border border-line px-2 py-0.5">LangGraph</span>
            <span className="rounded border border-line px-2 py-0.5">MCP</span>
            <span className="rounded border border-line px-2 py-0.5">ChromaDB</span>
          </div>
        </div>
      </div>
    </header>
  );
}
