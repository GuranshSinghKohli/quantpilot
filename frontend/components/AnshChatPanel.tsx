"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { ApiError, sendAnshChat } from "@/lib/api";

type Horizon = "short" | "long";
type RiskTolerance = "conservative" | "moderate" | "aggressive";
type Step = "horizon" | "risk" | "goal" | "chat";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

interface AnshChatPanelProps {
  onClose?: () => void;
  holdingsCount?: number;
}

const GREETING =
  "Hey — I'm ANSH, your OpenClaw portfolio advisor. Before I opine on the basket: are you thinking short-term or long-term?";

export default function AnshChatPanel({
  onClose,
  holdingsCount = 0,
}: AnshChatPanelProps) {
  const [step, setStep] = useState<Step>("horizon");
  const [horizon, setHorizon] = useState<Horizon | null>(null);
  const [risk, setRisk] = useState<RiskTolerance | null>(null);
  const [goal, setGoal] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", text: GREETING },
  ]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, loading, step]);

  async function requestOpinion(nextGoal: string) {
    if (!horizon || !risk) return;
    const goalText = nextGoal.trim() || "General portfolio review";
    setGoal(goalText);
    setLoading(true);
    setError(null);

    const prompt =
      `I want a portfolio opinion.\n` +
      `Looking for: ${goalText}\n` +
      `Horizon: ${horizon}-term\n` +
      `Risk: ${risk}\n` +
      `Please review my current basket and give your take.`;

    setMessages((prev) => [
      ...prev,
      { role: "user", text: prompt },
    ]);

    try {
      const history = messages.map((m) => ({
        role: m.role,
        content: m.text,
      }));
      const res = await sendAnshChat({
        message: prompt,
        horizon,
        risk_tolerance: risk,
        goal: goalText,
        history,
      });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: res.answer },
      ]);
      setStep("chat");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "ANSH could not analyze the portfolio. Is the API running?"
      );
    } finally {
      setLoading(false);
    }
  }

  async function askFollowUp(raw: string) {
    const q = raw.trim();
    if (!q || loading || !horizon || !risk) return;
    setLoading(true);
    setError(null);
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setDraft("");

    try {
      const history = [...messages, { role: "user" as const, text: q }].map(
        (m) => ({ role: m.role, content: m.text })
      );
      const res = await sendAnshChat({
        message: q,
        horizon,
        risk_tolerance: risk,
        goal: goal || "General portfolio review",
        history: history.slice(0, -1),
      });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: res.answer },
      ]);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "ANSH chat failed. Try again."
      );
    } finally {
      setLoading(false);
    }
  }

  function pickHorizon(value: Horizon) {
    setHorizon(value);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: value === "short" ? "Short-term" : "Long-term" },
      {
        role: "assistant",
        text: "Got it. What's your risk comfort — conservative, moderate, or aggressive?",
      },
    ]);
    setStep("risk");
  }

  function pickRisk(value: RiskTolerance) {
    setRisk(value);
    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        text: value.charAt(0).toUpperCase() + value.slice(1),
      },
      {
        role: "assistant",
        text:
          holdingsCount === 0
            ? "What are you looking for in this portfolio? (growth, income, capital preservation…) — also add holdings if the basket is empty so I have something to review."
            : "What are you looking for — growth, income, capital preservation, or something else? Type a short goal, then I'll analyze the basket.",
      },
    ]);
    setStep("goal");
  }

  function handleGoalSubmit(e: FormEvent) {
    e.preventDefault();
    void requestOpinion(draft);
    setDraft("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (step === "goal") {
        void requestOpinion(draft);
        setDraft("");
      } else if (step === "chat") {
        void askFollowUp(draft);
      }
    }
  }

  return (
    <section
      className="rounded-xl border border-hud bg-letterbox px-4 py-4"
      aria-label="ANSH portfolio advisor"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-300/80">
            OpenClaw · Portfolio advisor
          </p>
          <h4 className="font-display mt-0.5 text-lg font-semibold text-osd">
            ANSH
          </h4>
          <p className="mt-0.5 text-xs text-readout">
            Asks horizon, risk, and goals — then opines on your basket.
          </p>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-hud px-2 py-1 text-xs text-readout hover:text-osd"
          >
            Close
          </button>
        )}
      </div>

      <div
        ref={listRef}
        data-lenis-prevent
        className="mt-3 max-h-72 space-y-2.5 overflow-y-auto rounded-2xl border border-hud bg-hud-deep px-3 py-3"
      >
        {messages.map((msg, i) => (
          <div
            key={`${msg.role}-${i}-${msg.text.slice(0, 16)}`}
            className={`rounded-lg px-3 py-2 text-sm leading-relaxed ${
              msg.role === "user"
                ? "ml-6 border border-emerald-500/25 bg-emerald-500/10 text-emerald-50"
                : "mr-4 border border-hud bg-hud-deep text-osd"
            }`}
          >
            <p className="text-[10px] font-semibold uppercase tracking-wide text-readout">
              {msg.role === "user" ? "You" : "ANSH"}
            </p>
            <p className="mt-1 whitespace-pre-wrap">{msg.text}</p>
          </div>
        ))}
        {loading && (
          <p className="text-sm text-emerald-200/80" role="status">
            ANSH is reviewing the basket…
          </p>
        )}
      </div>

      {step === "horizon" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={loading}
            onClick={() => pickHorizon("short")}
            className="bk bk-quiet bk-snap px-3 py-1.5 text-xs text-osd/90 transition-colors hover:text-caution"
          >
            Short-term
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={() => pickHorizon("long")}
            className="bk bk-quiet bk-snap px-3 py-1.5 text-xs text-osd/90 transition-colors hover:text-caution"
          >
            Long-term
          </button>
        </div>
      )}

      {step === "risk" && (
        <div className="mt-3 flex flex-wrap gap-2">
          {(
            [
              ["conservative", "Conservative"],
              ["moderate", "Moderate"],
              ["aggressive", "Aggressive"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              disabled={loading}
              onClick={() => pickRisk(value)}
              className="bk bk-quiet bk-snap px-3 py-1.5 text-xs text-readout transition-colors hover:text-osd"
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {(step === "goal" || step === "chat") && (
        <form
          onSubmit={
            step === "goal"
              ? handleGoalSubmit
              : (e) => {
                  e.preventDefault();
                  void askFollowUp(draft);
                }
          }
          className="mt-3 space-y-2"
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder={
              step === "goal"
                ? "e.g. Long-term growth with limited drawdowns…"
                : "Ask ANSH a follow-up…"
            }
            className="w-full resize-y rounded-xl border border-hud bg-letterbox px-3 py-2.5 text-sm text-osd outline-none placeholder:text-readout focus:border-down"
          />
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={loading || (step === "goal" ? false : !draft.trim())}
              className="rounded-lg border border-emerald-500/40 bg-emerald-500/20 px-4 py-2 text-sm font-semibold text-emerald-50 hover:bg-emerald-500/30 disabled:opacity-50"
            >
              {loading
                ? "Thinking…"
                : step === "goal"
                  ? "Analyze portfolio"
                  : "Ask ANSH"}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="mt-2 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <p className="mt-3 text-[10px] leading-relaxed text-readout">
        ANSH opinions are educational only — not financial advice.
      </p>
    </section>
  );
}
