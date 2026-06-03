"use client";

import { useState } from "react";
import type { AnalysisResponse } from "@/types";
import { ApiError, sendChatMessage } from "@/lib/api";

interface ResearchChatPanelProps {
  ticker: string;
  analysis: AnalysisResponse;
}

export default function ResearchChatPanel({
  ticker,
  analysis,
}: ResearchChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [sources, setSources] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || loading) return;

    setLoading(true);
    setError(null);
    try {
      const res = await sendChatMessage(ticker, q, analysis as unknown as Record<string, unknown>);
      setAnswer(res.answer);
      setSources(res.sources_used);
      setQuestion("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Chat failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card-surface p-6">
      <h3 className="mb-1 text-sm font-semibold uppercase tracking-wider text-slate-400">
        Research Chat
      </h3>
      <p className="mb-4 text-xs text-slate-500">
        Ask follow-up questions grounded in this report and past analyses (RAG).
      </p>

      <form onSubmit={handleAsk} className="flex flex-col gap-3 sm:flex-row">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={`Ask about ${ticker}…`}
          className="flex-1 rounded-lg border border-[#1e1e2e] bg-[#0a0a0f] px-4 py-2.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-accent focus:outline-none"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
        >
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && (
        <p className="mt-3 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      {answer && (
        <div className="mt-5 rounded-lg border border-[#1e1e2e] bg-[#0a0a0f] p-4">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
            {answer}
          </p>
          {sources.length > 0 && (
            <p className="mt-3 text-xs text-slate-500">
              Sources: {sources.join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
