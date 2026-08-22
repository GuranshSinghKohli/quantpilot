"use client";

import { ArrowRight } from "lucide-react";
import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Bracketed,
  ColumnHeader,
  HudButton,
} from "@/components/hud/primitives";
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    setDraft("");
    setError(null);
    setLoading(false);
    // Do not autofocus — that scrolls the page away from the search bar.
  }, [investigationId]);

  useEffect(() => {
    // Keep scroll inside the message list only (never scroll the page).
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, loading]);

  async function ask(raw: string) {
    const q = raw.trim();
    if (!q || loading) return;

    setLoading(true);
    setError(null);
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setDraft("");

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
    e.stopPropagation();
    void ask(draft);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void ask(draft);
    }
  }

  return (
    <Bracketed
      as="section"
      large
      className="relative z-20 p-4 sm:p-5"
      aria-label={`Ask about the ${ticker} case`}
    >
      <ColumnHeader aside={ticker}>Ask about this case</ColumnHeader>

      {/* A full-bleed textarea gave a 1300px line and an empty box. The thread
          keeps a readable measure; the width goes to what you can ask. */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:gap-10">
        <div className="min-w-0">
          {messages.length > 0 || loading ? (
            <div
              ref={listRef}
              data-lenis-prevent
              className="mb-4 max-h-72 space-y-4 overflow-y-auto pr-1"
            >
              {messages.map((msg, i) => (
                <div
                  key={`${msg.role}-${i}-${msg.text.slice(0, 12)}`}
                  className={
                    msg.role === "assistant"
                      ? "bg-hud-deep px-3.5 py-3"
                      : "px-1"
                  }
                >
                  <p className="osd-label">
                    {msg.role === "user" ? "You asked" : "QuantPilot"}
                  </p>
                  <p className="mt-1.5 whitespace-pre-wrap text-[0.8125rem] leading-relaxed text-osd/90">
                    {msg.text}
                  </p>
                  {msg.sources && msg.sources.length > 0 && (
                    <p className="osd-label mt-2.5 border-t border-hud pt-2">
                      Sources: {msg.sources.join(" · ")}
                    </p>
                  )}
                </div>
              ))}
              {loading && (
                <p className="osd-label px-1" role="status">
                  Reading the case…
                </p>
              )}
            </div>
          ) : null}

          {/* Standalone form — never nested; input stays live while loading */}
          {/* Field and button are siblings, never a button inside the field's
              brackets — nested brackets read as a mistake. */}
          <form
            onSubmit={handleSubmit}
            autoComplete="off"
            className="flex items-stretch gap-2"
          >
            <label htmlFor={`inv-chat-${investigationId}`} className="sr-only">
              Your follow-up question
            </label>
            <Bracketed snap className="relative z-30 min-w-0 flex-1 px-3 py-2">
              <textarea
                id={`inv-chat-${investigationId}`}
                ref={inputRef}
                name={`investigation-chat-${investigationId}`}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder={`Why did ${ticker} move?`}
                rows={1}
                autoComplete="off"
                spellCheck
                className="min-h-[1.75rem] w-full resize-none bg-transparent text-[0.8125rem] leading-relaxed text-osd caret-caution outline-none placeholder:text-readout"
              />
            </Bracketed>
            <HudButton
              type="submit"
              variant="secondary"
              loading={loading}
              disabled={!draft.trim()}
              className="min-h-0 shrink-0 px-3"
            >
              {loading ? "Reading…" : "Ask"}
            </HudButton>
          </form>

          <p className="osd-label mt-2">
            Enter sends · Shift+Enter adds a line
          </p>

          {error && (
            <p className="mt-2 text-xs text-caution" role="alert">
              {error}
            </p>
          )}

          <p className="mt-4 border-t border-hud/70 pt-3 text-xs leading-relaxed text-readout">
            Every answer is drawn from this case alone — its summary, claims,
            and receipts — so you can check each reply against the evidence
            above.
          </p>
        </div>

        <div className="min-w-0">
          <p className="osd-label">Try asking</p>
          <ul className="mt-2 divide-y divide-hud/70 border-t border-hud/70">
            {SUGGESTIONS.map((s) => (
              <li key={s}>
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void ask(s)}
                  className="group flex w-full items-center justify-between gap-3 px-1 py-2.5 text-left text-xs text-osd/85 transition-colors hover:text-caution disabled:opacity-50"
                >
                  {s}
                  <ArrowRight
                    className="h-3.5 w-3.5 shrink-0 text-readout transition-colors group-hover:text-caution"
                    strokeWidth={1.5}
                    aria-hidden
                  />
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Bracketed>
  );
}
