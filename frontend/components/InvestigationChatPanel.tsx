"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, sendInvestigationChat } from "@/lib/api";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  sources?: string[];
}

interface InvestigationChatPanelProps {
  investigationId: number;
  ticker: string;
}

const SUGGESTIONS = [
  "Why did it move?",
  "What evidence supports the lead cause?",
  "What did Devil's Advocate challenge?",
];

export default function InvestigationChatPanel({
  investigationId,
  ticker,
}: InvestigationChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    setQuestion("");
    setError(null);
  }, [investigationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, loading]);

  async function ask(raw: string) {
    const q = raw.trim();
    if (!q || loading) return;

    setLoading(true);
    setError(null);
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setQuestion("");

    try {
      const res = await sendInvestigationChat(investigationId, q);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: res.answer,
          sources: res.sources_used,
        },
      ]);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Chat failed. Check the API is running."
      );
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(question);
  }

  return (
    <section
      className="rounded-xl border border-emerald-500/25 bg-gradient-to-br from-emerald-500/[0.06] to-transparent px-4 py-4"
      aria-label="Investigation RAG chat"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-300/80">
        Ask the case
      </p>
      <h5 className="font-display mt-1 text-base font-semibold text-white">
        RAG chat · ${ticker}
      </h5>
      <p className="mt-1 text-xs text-slate-500">
        Ask follow-ups grounded in this investigation&apos;s summary, claims,
        evidence, and related ledger cases.
      </p>

      <div className="mt-3 max-h-72 space-y-2.5 overflow-y-auto rounded-lg border border-white/[0.06] bg-black/25 px-3 py-3">
        {messages.length === 0 && !loading && (
          <p className="text-sm text-slate-500">
            Try a suggestion below, or type your own question.
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={`${msg.role}-${i}-${msg.text.slice(0, 12)}`}
            className={`rounded-lg px-3 py-2 text-sm leading-relaxed ${
              msg.role === "user"
                ? "ml-6 border border-emerald-500/25 bg-emerald-500/10 text-emerald-50"
                : "mr-6 border border-white/[0.06] bg-white/[0.03] text-slate-200"
            }`}
          >
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              {msg.role === "user" ? "You" : "QuantPilot"}
            </p>
            <p className="mt-1 whitespace-pre-wrap">{msg.text}</p>
            {msg.sources && msg.sources.length > 0 && (
              <p className="mt-2 text-[10px] text-slate-600">
                Sources: {msg.sources.join(" · ")}
              </p>
            )}
          </div>
        ))}
        {loading && (
          <p className="text-sm text-emerald-200/80" role="status">
            Searching case memory…
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={loading}
            onClick={() => void ask(s)}
            className="rounded-full border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[11px] text-slate-400 transition hover:border-emerald-500/30 hover:text-emerald-100 disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="mt-3 flex flex-col gap-2 sm:flex-row">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={`Ask about $${ticker} in this case…`}
          disabled={loading}
          className="min-w-0 flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-sm text-white outline-none focus:border-emerald-500/40 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-4 py-2.5 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-500/25 disabled:opacity-50"
        >
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && (
        <p className="mt-2 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
