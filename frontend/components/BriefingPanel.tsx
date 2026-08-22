"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { AuthUser, DailyBriefing } from "@/types";
import {
  ApiError,
  fetchLatestBriefing,
  generateBriefing,
} from "@/lib/api";

interface BriefingPanelProps {
  user: AuthUser | null;
  onSelectTicker?: (ticker: string) => void;
}

function formatWhen(iso: string) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export default function BriefingPanel({
  user,
  onSelectTicker,
}: BriefingPanelProps) {
  const [data, setData] = useState<DailyBriefing | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const briefing = await fetchLatestBriefing();
      setData(briefing);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setData(null);
        setError(null);
      } else {
        setError(
          err instanceof ApiError ? err.message : "Could not load briefing."
        );
      }
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate() {
    if (!user) return;
    setGenerating(true);
    setError(null);
    try {
      const briefing = await generateBriefing();
      setData(briefing);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Briefing generation failed."
      );
    } finally {
      setGenerating(false);
    }
  }

  if (!user) {
    return (
      <div className="card-surface p-5">
        <h3 className="panel-title">daily briefing</h3>
        <p className="mt-3 text-sm leading-relaxed text-readout">
          Sign in to get an AI daily briefing across your saved holdings.
          It refreshes on a schedule and is available anytime in this panel.
        </p>
      </div>
    );
  }

  return (
    <div className="card-surface card-surface-hover p-5">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <h3 className="panel-title">daily briefing</h3>
          <p className="mt-0.5 text-[11px] text-readout">
            portfolio agent · overnight monitor
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading || generating}
            aria-label="Reload briefing"
            className="bk bk-quiet bk-snap px-2.5 py-2 text-readout transition-colors hover:text-caution disabled:opacity-50"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
              strokeWidth={1.5}
              aria-hidden
            />
          </button>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={generating || loading}
            className="rounded-lg border border-hud bg-caution-50 px-2.5 py-1 text-xs font-medium text-down transition hover:bg-osd hover:text-letterbox disabled:opacity-50"
          >
            {generating ? "writing…" : "Generate now"}
          </button>
        </div>
      </div>

      {loading && !data && (
        <div className="space-y-2">
          <div className="skeleton-shimmer h-10 rounded-lg" />
          <div className="skeleton-shimmer h-20 rounded-lg" />
        </div>
      )}

      {error && (
        <p className="mb-3 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      {!loading && !data && !error && (
        <p className="py-4 text-center text-sm leading-relaxed text-readout">
          no briefing yet
          <br />
          <span className="text-readout">
            add holdings, then hit generate, or wait for the daily job
          </span>
        </p>
      )}

      {data && (
        <div className="space-y-4">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-readout">
              {formatWhen(data.generated_at)} · {data.status}
              {data.confidence_score != null && (
                <> · conf {(data.confidence_score * 100).toFixed(0)}%</>
              )}
            </p>
            <h4 className="font-display mt-1 text-lg font-semibold text-osd">
              {data.headline || "Portfolio briefing"}
            </h4>
            <p className="mt-2 text-sm leading-relaxed text-readout">
              {data.summary}
            </p>
          </div>

          {data.highlights.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-readout">
                highlights
              </p>
              <ul className="mt-2 space-y-1.5">
                {data.highlights.map((item) => (
                  <li
                    key={item}
                    className="rounded-lg border border-hud bg-letterbox px-3 py-2 text-xs text-readout"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.risks.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-readout">
                risks
              </p>
              <ul className="mt-2 space-y-1.5">
                {data.risks.map((item) => (
                  <li
                    key={item}
                    className="rounded-lg border border-red-500/15 bg-red-500/5 px-3 py-2 text-xs text-red-200/90"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.watch_tickers.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <span className="w-full text-[10px] uppercase tracking-wider text-readout">
                watch today
              </span>
              {data.watch_tickers.map((ticker) => (
                <button
                  key={ticker}
                  type="button"
                  onClick={() => onSelectTicker?.(ticker)}
                  className="bk bk-quiet bk-snap px-3 py-1 text-xs text-osd/90 transition-colors hover:text-caution"
                >
                  {ticker}
                </button>
              ))}
            </div>
          )}

          <p className="text-[10px] leading-relaxed text-readout">
            {data.disclaimer}
          </p>
        </div>
      )}
    </div>
  );
}
