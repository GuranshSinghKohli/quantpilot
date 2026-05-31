"use client";

import { useState } from "react";
import type { AnalysisResponse, FinalReport, StoredReport } from "@/types";
import { fetchPastReports } from "@/lib/api";

interface ReportDisplayProps {
  analysis: AnalysisResponse;
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

export default function ReportDisplay({ analysis }: ReportDisplayProps) {
  const { final_report: report } = analysis;
  const confidence = analysis.overall_confidence_score ?? 0;
  const confidencePct = Math.round(confidence * 100);
  const recStyle = getRecommendationStyle(report.recommendation);
  const [openSections, setOpenSections] = useState<Set<number>>(
    () => new Set(report.sections.map((_, i) => i))
  );
  const [showPast, setShowPast] = useState(false);
  const [pastReports, setPastReports] = useState<StoredReport[]>([]);
  const [pastLoading, setPastLoading] = useState(false);
  const [copied, setCopied] = useState(false);

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
          <h2 className="text-2xl font-bold text-white">{report.report_title}</h2>
          <p className="mt-1 text-sm text-slate-500">
            AI-generated research report · {analysis.ticker}
          </p>
          {analysis.overall_confidence_score != null && (
            <div
              className="mt-3 max-w-md"
              title="Based on data completeness and source availability"
            >
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Analysis Confidence: {confidencePct}%</span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-[#1e1e2e]">
                <div
                  className={`h-full rounded-full transition-all ${confidenceColor(confidence)}`}
                  style={{ width: `${confidencePct}%` }}
                />
              </div>
            </div>
          )}
        </div>
        <span
          className={`rounded-lg border px-4 py-2 text-sm font-bold ${recStyle.bg} ${recStyle.text}`}
        >
          {recStyle.label}
        </span>
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

      <div className="mt-6 rounded-xl border border-accent/30 bg-accent/5 p-5">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-accent">
          Executive Summary
        </h3>
        <p className="mt-3 leading-relaxed text-slate-300">
          {report.executive_summary}
        </p>
      </div>

      <div className="mt-4 space-y-2">
        {report.sections.map((section, index) => (
          <div
            key={section.title + index}
            className="overflow-hidden rounded-lg border border-[#1e1e2e]"
          >
            <button
              type="button"
              onClick={() => toggleSection(index)}
              className="flex w-full items-center justify-between bg-[#0a0a0f]/50 px-4 py-3 text-left transition hover:bg-[#1e1e2e]/30"
            >
              <span className="font-medium text-slate-200">{section.title}</span>
              <span className="text-slate-500">
                {openSections.has(index) ? "−" : "+"}
              </span>
            </button>
            {openSections.has(index) && (
              <div className="border-t border-[#1e1e2e] px-4 py-3 text-sm leading-relaxed text-slate-400">
                {section.content}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="mt-6 rounded-lg border border-[#1e1e2e] bg-[#0a0a0f]/50 p-4">
        <p className="text-xs text-slate-500">Recommendation</p>
        <p className="mt-1 text-slate-300">{report.recommendation}</p>
      </div>

      <div className="mt-6 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-lg border border-[#1e1e2e] bg-[#12121a] px-4 py-2 text-sm text-slate-300 transition hover:border-accent hover:text-accent"
        >
          {copied ? "Copied!" : "Copy Report"}
        </button>
        <button
          type="button"
          onClick={handleViewPast}
          className="rounded-lg border border-[#1e1e2e] bg-[#12121a] px-4 py-2 text-sm text-slate-300 transition hover:border-accent hover:text-accent"
        >
          View Past Reports
        </button>
      </div>

      <p className="mt-6 text-xs text-slate-600">{report.disclaimer}</p>

      {showPast && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="card-surface max-h-[80vh] w-full max-w-2xl overflow-hidden">
            <div className="flex items-center justify-between border-b border-[#1e1e2e] p-4">
              <h3 className="font-semibold text-white">
                Past Reports — {analysis.ticker}
              </h3>
              <button
                type="button"
                onClick={() => setShowPast(false)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>
            <div className="max-h-[60vh] overflow-y-auto p-4">
              {pastLoading && (
                <p className="text-sm text-slate-500">Loading…</p>
              )}
              {!pastLoading && pastReports.length === 0 && (
                <p className="text-sm text-slate-500">No past reports stored.</p>
              )}
              {pastReports.map((item) => (
                <div
                  key={item.id}
                  className="mb-3 rounded-lg border border-[#1e1e2e] p-4"
                >
                  <p className="text-sm font-medium text-accent">
                    {item.report.report_title}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {(item.metadata.timestamp as string) ?? "Unknown date"}
                  </p>
                  <p className="mt-2 line-clamp-3 text-sm text-slate-400">
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
