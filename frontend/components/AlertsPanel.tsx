"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { AlertEvent, AlertRule, AuthUser } from "@/types";
import {
  ApiError,
  createAlertRule,
  deleteAlertRule,
  evaluateAlerts,
  fetchAlertEvents,
  fetchAlertRules,
  markAlertRead,
  markAllAlertsRead,
} from "@/lib/api";

interface AlertsPanelProps {
  user: AuthUser | null;
  onSelectTicker?: (ticker: string) => void;
  onUnreadChange?: (count: number) => void;
}

type FormAlertType = AlertRule["alert_type"];

const ALERT_TYPES: { value: FormAlertType; label: string }[] = [
  { value: "price_above", label: "Price above ($)" },
  { value: "price_below", label: "Price below ($)" },
  { value: "volatility_pct", label: "Daily move (%)" },
  { value: "news_sentiment", label: "News sentiment" },
];

const SENTIMENT_STRENGTHS = [
  { value: "0.2", label: "Mild (0.2)" },
  { value: "0.3", label: "Medium (0.3)" },
  { value: "0.5", label: "Strong (0.5)" },
  { value: "0.7", label: "Very strong (0.7)" },
];

function defaultThreshold(type: FormAlertType): string {
  if (type === "price_above" || type === "price_below") return "150";
  if (type === "volatility_pct") return "3";
  return "0.3";
}

function thresholdHint(type: FormAlertType): string {
  if (type === "price_above") return "Alert when price rises to this dollar level.";
  if (type === "price_below") return "Alert when price falls to this dollar level.";
  if (type === "volatility_pct")
    return "Alert when absolute daily move reaches this percent (e.g. 3 = ±3%).";
  return "Score is from -1 (bearish) to +1 (bullish) based on recent headlines.";
}

function formatRuleThreshold(rule: AlertRule): string {
  const t = rule.threshold;
  if (rule.alert_type === "price_above") return `price ≥ $${t}`;
  if (rule.alert_type === "price_below") return `price ≤ $${t}`;
  if (rule.alert_type === "volatility_pct") return `daily move ≥ ±${Math.abs(t)}%`;
  if (rule.alert_type === "news_sentiment") {
    if (t >= 0) return `bullish news ≥ ${t}`;
    return `bearish news ≤ ${t}`;
  }
  return `${rule.alert_type} @ ${t}`;
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

export default function AlertsPanel({
  user,
  onSelectTicker,
  onUnreadChange,
}: AlertsPanelProps) {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ticker, setTicker] = useState("");
  const [alertType, setAlertType] = useState<FormAlertType>("price_above");
  const [threshold, setThreshold] = useState("150");
  const [sentimentDirection, setSentimentDirection] = useState<"bullish" | "bearish">(
    "bullish"
  );

  const isSentiment = alertType === "news_sentiment";

  const load = useCallback(async () => {
    if (!user) {
      setRules([]);
      setEvents([]);
      onUnreadChange?.(0);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [r, e] = await Promise.all([
        fetchAlertRules(),
        fetchAlertEvents(false),
      ]);
      setRules(r);
      setEvents(e);
      onUnreadChange?.(e.filter((x) => !x.is_read).length);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load alerts.");
    } finally {
      setLoading(false);
    }
  }, [user, onUnreadChange]);

  useEffect(() => {
    load();
  }, [load]);

  function handleAlertTypeChange(next: FormAlertType) {
    setAlertType(next);
    setThreshold(defaultThreshold(next));
    if (next === "news_sentiment") {
      setSentimentDirection("bullish");
    }
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!user) return;
    setError(null);

    let value = Number(threshold);
    if (Number.isNaN(value)) {
      setError("Enter a valid number for the threshold.");
      return;
    }
    if (isSentiment) {
      value = Math.abs(value);
      if (value <= 0 || value > 1) {
        setError("News sentiment strength must be between 0.1 and 1.0.");
        return;
      }
      if (sentimentDirection === "bearish") value = -value;
    }

    try {
      await createAlertRule({
        ticker,
        alert_type: alertType,
        threshold: value,
      });
      setTicker("");
      setThreshold(defaultThreshold(alertType));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create rule.");
    }
  }

  async function handleDelete(id: number) {
    try {
      await deleteAlertRule(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete rule.");
    }
  }

  async function handleEvaluate() {
    setEvaluating(true);
    setError(null);
    try {
      await evaluateAlerts();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Evaluation failed.");
    } finally {
      setEvaluating(false);
    }
  }

  async function handleMarkRead(id: number) {
    try {
      await markAlertRead(id);
      await load();
    } catch {
      // non-blocking
    }
  }

  async function handleMarkAll() {
    try {
      await markAllAlertsRead();
      await load();
    } catch {
      // non-blocking
    }
  }

  if (!user) {
    return (
      <div className="card-surface p-5">
        <h3 className="panel-title">smart alerts</h3>
        <p className="mt-3 text-sm leading-relaxed text-slate-500">
          Sign in to set price, volatility, and news-sentiment alerts on your
          holdings. Fired alerts show up here in-app.
        </p>
      </div>
    );
  }

  return (
    <div className="card-surface card-surface-hover space-y-5 p-5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="panel-title">smart alerts</h3>
          <p className="mt-0.5 text-[11px] text-slate-600">
            redis-backed cache · checks every few minutes
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading || evaluating}
            className="rounded-lg border border-white/[0.08] px-2.5 py-1 text-xs text-slate-400 hover:text-violet-300 disabled:opacity-50"
          >
            {loading ? "…" : "↻"}
          </button>
          <button
            type="button"
            onClick={handleEvaluate}
            disabled={evaluating || loading}
            className="rounded-lg border border-violet-500/30 bg-violet-500/10 px-2.5 py-1 text-xs font-medium text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
          >
            {evaluating ? "checking…" : "Check now"}
          </button>
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <form onSubmit={handleCreate} className="space-y-2">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <input
            required
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="TICKER"
            className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40"
          />
          <select
            value={alertType}
            onChange={(e) =>
              handleAlertTypeChange(e.target.value as FormAlertType)
            }
            className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40"
          >
            {ALERT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>

          {isSentiment ? (
            <>
              <select
                value={sentimentDirection}
                onChange={(e) =>
                  setSentimentDirection(e.target.value as "bullish" | "bearish")
                }
                className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40"
              >
                <option value="bullish">Bullish headlines</option>
                <option value="bearish">Bearish headlines</option>
              </select>
              <select
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40"
              >
                {SENTIMENT_STRENGTHS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <input
              required
              type="number"
              step={alertType === "volatility_pct" ? "0.1" : "any"}
              min={alertType === "volatility_pct" ? "0.1" : undefined}
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder={
                alertType === "volatility_pct" ? "e.g. 3" : "e.g. 150"
              }
              className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500/40 sm:col-span-1 lg:col-span-1"
            />
          )}

          <button
            type="submit"
            className="rounded-lg bg-gradient-to-r from-violet-600 to-cyan-600 px-3 py-2 text-sm font-medium text-white sm:col-span-2 lg:col-span-4"
          >
            Add rule
          </button>
        </div>
        <p className="text-[10px] leading-relaxed text-slate-600">
          {thresholdHint(alertType)}
          {isSentiment
            ? ` Example: ${sentimentDirection} + medium alerts when the headline score hits ${
                sentimentDirection === "bullish" ? "+" : "-"
              }${threshold}.`
            : null}
        </p>
      </form>

      <div>
        <p className="text-[10px] uppercase tracking-wider text-slate-600">
          rules ({rules.length})
        </p>
        {rules.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No rules yet.</p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {rules.map((rule) => (
              <li
                key={rule.id}
                className="flex items-center justify-between gap-2 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-xs"
              >
                <button
                  type="button"
                  onClick={() => onSelectTicker?.(rule.ticker)}
                  className="text-left text-slate-200 hover:text-violet-200"
                >
                  <span className="font-display font-semibold">{rule.ticker}</span>{" "}
                  <span className="text-slate-500">
                    {formatRuleThreshold(rule)}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(rule.id)}
                  className="text-slate-500 hover:text-red-300"
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="flex items-center justify-between">
          <p className="text-[10px] uppercase tracking-wider text-slate-600">
            notifications
          </p>
          {events.some((e) => !e.is_read) && (
            <button
              type="button"
              onClick={handleMarkAll}
              className="text-[10px] text-violet-300 hover:underline"
            >
              mark all read
            </button>
          )}
        </div>
        {events.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No alerts fired yet. Add a rule and hit Check now.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {events.map((event) => (
              <li
                key={event.id}
                className={`rounded-xl border px-3 py-2.5 ${
                  event.is_read
                    ? "border-white/[0.04] bg-white/[0.01]"
                    : "border-violet-500/25 bg-violet-500/5"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <button
                      type="button"
                      onClick={() => onSelectTicker?.(event.ticker)}
                      className="font-display text-sm font-semibold text-white hover:text-violet-200"
                    >
                      {event.title}
                    </button>
                    <p className="mt-1 text-xs leading-relaxed text-slate-400">
                      {event.message}
                    </p>
                    <p className="mt-1 text-[10px] text-slate-600">
                      {formatWhen(event.created_at)}
                    </p>
                  </div>
                  {!event.is_read && (
                    <button
                      type="button"
                      onClick={() => handleMarkRead(event.id)}
                      className="shrink-0 text-[10px] text-violet-300 hover:underline"
                    >
                      mark read
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
