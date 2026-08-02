import AuthPanel from "@/components/AuthPanel";
import type { AuthUser } from "@/types";

interface AppHeaderProps {
  apiReachable: boolean | null;
  user: AuthUser | null;
  onUserChange: (user: AuthUser | null) => void;
  sourcesAvailable?: boolean;
  onSourcesClick?: () => void;
}

export default function AppHeader({
  apiReachable,
  user,
  onUserChange,
  sourcesAvailable = false,
  onSourcesClick,
}: AppHeaderProps) {
  return (
    <header className="sticky top-0 z-40 border-b border-white/[0.06] bg-[#07070d]/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-4 py-3 sm:gap-4 sm:px-6 lg:px-8">
        <div className="min-w-0 flex-1">
          <h1 className="font-display truncate text-lg font-bold tracking-tight sm:text-xl">
            <span className="text-white">Quant</span>
            <span className="gradient-text">Pilot</span>
          </h1>
          <p className="hidden truncate text-xs text-slate-500 sm:block">
            why did it move · evidence over vibes
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <button
            type="button"
            onClick={onSourcesClick}
            disabled={!sourcesAvailable}
            className="group hidden items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-xs font-medium text-slate-400 transition hover:border-cyan-500/30 hover:bg-cyan-500/[0.06] hover:text-cyan-200 disabled:cursor-not-allowed disabled:opacity-35 sm:flex"
            title={
              sourcesAvailable
                ? "Jump to data sources and provenance"
                : "Run an analysis to view sources"
            }
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              className="h-3.5 w-3.5 text-cyan-400/80"
              aria-hidden="true"
            >
              <ellipse cx="12" cy="5" rx="7" ry="3" />
              <path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5" />
              <path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
            </svg>
            Sources
          </button>
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
          <AuthPanel user={user} onUserChange={onUserChange} />
        </div>
      </div>
    </header>
  );
}
