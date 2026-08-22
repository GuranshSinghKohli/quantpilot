"use client";

import { X } from "lucide-react";
import { useState } from "react";
import type {
  AnalysisResponse,
  FinalReport,
  SmartSummary,
  StoredReport,
} from "@/types";
import { ApiError, fetchPastReports, smartSummarizeText } from "@/lib/api";

interface ReportDisplayProps {
  analysis: AnalysisResponse;
  /** When true, hide summary/recommendation that the investment memo already shows. */
  omitOverlappingSummary?: boolean;
}

function getRecommendationStyle(rec: string): {
  bg: string;
  text: string;
  label: string;
} {
  const upper = rec.toUpperCase();
  if (upper.includes("BUY")) {
    return { bg: "bg-emerald-500/20 border-emerald-500/50", text: "text-emerald-400", label: "BUY" };
  }
  if (upper.includes("SELL")) {
    return { bg: "bg-red-500/20 border-red-500/50", text: "text-red-400", label: "SELL" };
  }
  return { bg: "bg-amber-500/20 border-amber-500/50", text: "text-amber-400", label: "HOLD" };
}

function reportToText(report: FinalReport): string {
  const lines = [
    report.report_title,
    "",
    "EXECUTIVE SUMMARY",
    report.executive_summary,
    "",
    ...report.sections.flatMap((s) => ["## " + s.title, s.content, ""]),
    "",
    "RECOMMENDATION: " + report.recommendation,
    "",
    report.disclaimer,
  ];
  return lines.join("\n");
}

function confidenceColor(score: number): string {
  if (score >= 0.8) return "bg-emerald-500";
  if (score >= 0.5) return "bg-amber-500";
  return "bg-red-500";
}

export default function ReportDisplay({
  analysis,
  omitOverlappingSummary = false,
}: ReportDisplayProps) {
  const { final_report: report } = analysis;
  const confidence = analysis.overall_confidence_score ?? 0;
  const confidencePct = Math.round(confidence * 100);
  const recStyle = getRecommendationStyle(report.recommendation);
  const [openSections, setOpenSections] = useState<Set<number>>(() =>
    omitOverlappingSummary
      ? new Set()
      : new Set(report.sections.map((_, i) => i))
  );
  const [showPast, setShowPast] = useState(false);
  const [pastReports, setPastReports] = useState<StoredReport[]>([]);
  const [pastLoading, setPastLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [smartSummary, setSmartSummary] = useState<SmartSummary | null>(null);
  const [summarizing, setSummarizing] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  function toggleSection(index: number) {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(reportToText(report));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function handleSmartSummarize() {
    setSummarizing(true);
    setSummaryError(null);
    try {
      const summary = await smartSummarizeText(
        report.report_title,
        reportToText(report)
      );
      setSmartSummary(summary);
    } catch (err) {
      setSummaryError(
        err instanceof ApiError
          ? err.message
          : "Smart Summarize failed. Try again."
      );
    } finally {
      setSummarizing(false);
    }
  }

  async function handleViewPast() {
    setShowPast(true);
    setPastLoading(true);
    try {
      const reports = await fetchPastReports(analysis.ticker);
      setPastReports(reports);
    } catch {
      setPastReports([]);
    } finally {
      setPastLoading(false);
    }
  }

  return (
    <div className="card-surface p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-2xl font-bold text-osd">{report.report_title}</h2>
          <p className="mt-1 text-sm text-readout">
            AI-generated research report · {analysis.ticker}
          </p>
          {analysis.overall_confidence_score != null && (
            <div
              className="mt-3 max-w-md"
              title="Based on data completeness and source availability"
            >
              <div className="flex items-center justify-between text-xs text-readout">
                <span>Analysis Confidence: {confidencePct}%</span>
              </div>
              <div className="mt-1 h-2 overflow-hidden bg-[#1e1e2e]">
                <div
                  className={`h-full transition-all ${confidenceColor(confidence)}`}
                  style={{ width: `${confidencePct}%` }}
                />
              </div>
            </div>
          )}
        </div>
        {!omitOverlappingSummary && (
          <span
            className={`rounded-lg border px-4 py-2 text-sm font-bold ${recStyle.bg} ${recStyle.text}`}
          >
            {recStyle.label}
          </span>
        )}
      </div>

      {analysis.validation_warnings && analysis.validation_warnings.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200/90">
          <p className="font-medium text-amber-300">Validation notes</p>
          <ul className="mt-1 list-inside list-disc text-amber-200/80">
            {analysis.validation_warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {!omitOverlappingSummary && (
        <div className="mt-6 rounded-xl border border-accent/30 bg-accent/5 p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-accent">
            Executive Summary
          </h3>
          <p className="mt-3 leading-relaxed text-readout">
            {report.executive_summary}
          </p>
        </div>
      )}

      {omitOverlappingSummary && (
        <p className="mt-4 text-xs leading-relaxed text-readout">
          Decision and thesis are in the investment memo above. This section has
          the detailed agent write-ups only.
        </p>
      )}

      <div className="mt-4 space-y-2">
        {report.sections.map((section, index) => (
          <div
            key={section.title + index}
            className="overflow-hidden rounded-lg border border-hud"
          >
            <button
              type="button"
              onClick={() => toggleSection(index)}
              className="flex w-full items-center justify-between bg-letterbox px-4 py-3 text-left transition hover:bg-hud-deep"
            >
              <span className="font-medium text-osd">{section.title}</span>
              <span className="text-readout">
                {openSections.has(index) ? "−" : "+"}
              </span>
            </button>
            {openSections.has(index) && (
              <div className="border-t border-hud px-4 py-3 text-sm leading-relaxed text-readout">
                {section.content}
              </div>
            )}
          </div>
        ))}
      </div>

      {!omitOverlappingSummary && (
        <div className="mt-6 rounded-lg border border-hud bg-letterbox p-4">
          <p className="text-xs text-readout">Recommendation</p>
          <p className="mt-1 text-readout">{report.recommendation}</p>
        </div>
      )}

      <div className="mt-6 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-lg border border-hud bg-[#12121a] px-4 py-2 text-sm text-readout transition hover:border-accent hover:text-accent"
        >
          {copied ? "Copied!" : "Copy Report"}
        </button>
        <button
          type="button"
          onClick={handleViewPast}
          className="rounded-lg border border-hud bg-[#12121a] px-4 py-2 text-sm text-readout transition hover:border-accent hover:text-accent"
        >
          View Past Reports
        </button>
        <button
          type="button"
          onClick={() => void handleSmartSummarize()}
          disabled={summarizing}
          className="rounded-lg border border-hud bg-caution-50 px-4 py-2 text-sm font-medium text-osd transition hover:bg-osd hover:text-letterbox disabled:opacity-50"
        >
          {summarizing ? "Summarizing…" : "Smart Summarize"}
        </button>
      </div>

      {summaryError && (
        <p className="mt-3 text-sm text-red-400" role="alert">
          {summaryError}
        </p>
      )}

      {smartSummary && (
        <div className="mt-4 rounded-xl border border-hud bg-caution-50 px-4 py-4 space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-readout">
            Smart summary
          </p>
          <p className="text-base font-medium text-osd">
            {smartSummary.headline}
          </p>
          <ul className="space-y-1.5">
            {smartSummary.bullets.map((bullet, i) => (
              <li
                key={`${i}-${bullet.slice(0, 24)}`}
                className="flex gap-2 text-sm text-readout"
              >
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 bg-violet-400" />
                <span>{bullet}</span>
              </li>
            ))}
          </ul>
          <p className="text-sm text-osd">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-readout">
              Takeaway ·{" "}
            </span>
            {smartSummary.takeaway}
          </p>
        </div>
      )}

      <p className="mt-6 text-xs text-readout">{report.disclaimer}</p>

      {showPast && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-osd/70 p-4">
          <div className="card-surface max-h-[80vh] w-full max-w-2xl overflow-hidden">
            <div className="flex items-center justify-between border-b border-hud p-4">
              <h3 className="font-semibold text-osd">
                Past Reports: {analysis.ticker}
              </h3>
              <button
                type="button"
                onClick={() => setShowPast(false)}
                aria-label="Close past reports"
                className="text-readout transition-colors hover:text-caution"
              >
                <X className="h-4 w-4" strokeWidth={1.5} aria-hidden />
              </button>
            </div>
            <div data-lenis-prevent className="max-h-[60vh] overflow-y-auto p-4">
              {pastLoading && (
                <p className="text-sm text-readout">Loading…</p>
              )}
              {!pastLoading && pastReports.length === 0 && (
                <p className="text-sm text-readout">No past reports stored.</p>
              )}
              {pastReports.map((item) => (
                <div
                  key={item.id}
                  className="mb-3 rounded-lg border border-hud p-4"
                >
                  <p className="text-sm font-medium text-accent">
                    {item.report.report_title}
                  </p>
                  <p className="mt-1 text-xs text-readout">
                    {(item.metadata.timestamp as string) ?? "Unknown date"}
                  </p>
                  <p className="mt-2 line-clamp-3 text-sm text-readout">
                    {item.report.executive_summary}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
