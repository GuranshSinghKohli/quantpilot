"use client";

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
        <p className="mt-3 text-sm leading-relaxed text-slate-500">
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
          <p className="mt-0.5 text-[11px] text-slate-600">
            portfolio agent · overnight monitor
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading || generating}
            className="rounded-lg border border-white/[0.08] px-2.5 py-1 text-xs text-slate-400 transition hover:border-violet-500/30 hover:text-violet-300 disabled:opacity-50"
          >
            {loading ? "…" : "↻"}
          </button>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={generating || loading}
            className="rounded-lg border border-violet-500/30 bg-violet-500/10 px-2.5 py-1 text-xs font-medium text-violet-200 transition hover:bg-violet-500/20 disabled:opacity-50"
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
        <p className="py-4 text-center text-sm leading-relaxed text-slate-500">
          no briefing yet
          <br />
          <span className="text-slate-600">
            add holdings, then hit generate, or wait for the daily job
          </span>
        </p>
      )}

      {data && (
        <div className="space-y-4">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-slate-600">
              {formatWhen(data.generated_at)} · {data.status}
              {data.confidence_score != null && (
                <> · conf {(data.confidence_score * 100).toFixed(0)}%</>
              )}
            </p>
            <h4 className="font-display mt-1 text-lg font-semibold text-white">
              {data.headline || "Portfolio briefing"}
            </h4>
            <p className="mt-2 text-sm leading-relaxed text-slate-300">
              {data.summary}
            </p>
          </div>

          {data.highlights.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-600">
                highlights
              </p>
              <ul className="mt-2 space-y-1.5">
                {data.highlights.map((item) => (
                  <li
                    key={item}
                    className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-xs text-slate-300"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.risks.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-600">
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
              <span className="w-full text-[10px] uppercase tracking-wider text-slate-600">
                watch today
              </span>
              {data.watch_tickers.map((ticker) => (
                <button
                  key={ticker}
                  type="button"
                  onClick={() => onSelectTicker?.(ticker)}
                  className="rounded-full border border-violet-500/30 bg-violet-500/10 px-3 py-1 text-xs font-medium text-violet-200 hover:bg-violet-500/20"
                >
                  {ticker}
                </button>
              ))}
            </div>
          )}

          <p className="text-[10px] leading-relaxed text-slate-600">
            {data.disclaimer}
          </p>
        </div>
      )}
    </div>
  );
}
