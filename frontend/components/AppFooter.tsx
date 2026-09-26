import { TapeCounter } from "@/components/hud/primitives";
import { INVESTIGATE_WINDOWS, type InvestigateWindow } from "@/components/SearchBar";
import { cn } from "@/lib/utils";

export default function AppFooter({
  caseId,
  windowLabel = "1d",
}: {
  caseId?: number | null;
  windowLabel?: InvestigateWindow | string;
}) {
  const counter = caseId != null ? String(caseId).padStart(6, "0") : "——————";
  const formatted = `${counter.slice(0, 2)}:${counter.slice(2)}`;

  return (
    <footer className="mt-4 border-t border-hud py-3">
      <div className="flex flex-col gap-3 px-3 sm:px-4 lg:flex-row lg:items-center lg:justify-between">
        <TapeCounter value={formatted} />

        <p className="order-last max-w-xl font-mono text-xs leading-relaxed tracking-wide text-readout lg:order-none">
          Systems project, not a product launch. Next.js + FastAPI + LangGraph.
          Evidence Ledger: planner → rank → verify → DA. yfinance + EDGAR.
          Not advice.
        </p>

        <div
          className="flex shrink-0 items-end gap-1"
          aria-label={`Active look-back window: ${windowLabel}`}
        >
          {INVESTIGATE_WINDOWS.map((w) => (
            <span
              key={w.value}
              className={cn(
                "w-1",
                w.value === windowLabel
                  ? "h-5 bg-caution shadow-neon"
                  : "h-3 bg-hud"
              )}
              aria-hidden
            />
          ))}
        </div>
      </div>
    </footer>
  );
}
