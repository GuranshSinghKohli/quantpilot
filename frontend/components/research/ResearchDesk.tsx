"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, fetchResearchJob, submitResearchJob } from "@/lib/api";
import {
  POLL_INTERVAL_MS,
  type CitationView,
  type FindingView,
  type ResearchDeskModel,
  pollResearch,
  MARKET_IDENTITY_UNAVAILABLE,
  researchModel,
} from "@/lib/researchDesk";
import { submitBody } from "@/lib/researchSubmit";

const PLACEHOLDER = "What recent NVIDIA developments coincided with notable market movement?";

export default function ResearchDesk() {
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState<"idle" | "submitting" | "polling" | "ready" | "error">("idle");
  const [model, setModel] = useState<ResearchDeskModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [researchId, setResearchId] = useState<string | null>(null);

  useEffect(() => {
    if (!researchId || phase !== "polling") return;
    const controller = new AbortController();
    let active = true;
    pollResearch(() => fetchResearchJob(researchId), {
      intervalMs: POLL_INTERVAL_MS,
      signal: controller.signal,
    })
      .then((next) => {
        if (!active) return;
        setModel(next);
        setPhase("ready");
      })
      .catch((err: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "Research status could not be read.");
        setPhase("error");
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [researchId, phase]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setModel(null);
    let body: { query: string };
    try {
      body = submitBody(query);
    } catch {
      setError("Enter a research question.");
      setPhase("error");
      return;
    }
    setPhase("submitting");
    try {
      const accepted = await submitResearchJob(body.query);
      setResearchId(accepted.research_id);
      setModel(
        researchModel({
          research_id: accepted.research_id,
          status: accepted.status,
          request: { query: body.query },
        })
      );
      setPhase("polling");
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Research could not be submitted.");
      setPhase("error");
    }
  }

  return (
    <main className="mx-auto min-h-screen w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-hud pb-6">
        <div>
          <h1 className="font-display text-2xl font-black uppercase tracking-wide text-osd sm:text-3xl">
            QuantPilot
          </h1>
          <p className="mt-2 max-w-xl font-sans text-sm leading-relaxed text-readout">
            Market and technology research. Claims stay tied to retrieved evidence. A market window is a measured return, not a cause.
          </p>
        </div>
        <Link href="/investigate" className="font-mono text-xs uppercase tracking-widest text-cyan underline-offset-4 hover:underline">
          Case desk
        </Link>
      </header>

      <form onSubmit={onSubmit} className="mt-8 space-y-3">
        <label htmlFor="research-query" className="block font-sans text-base text-osd">
          What would you like to research?
        </label>
        <textarea
          id="research-query"
          name="query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={PLACEHOLDER}
          rows={4}
          required
          className="w-full resize-y border border-hud bg-panel px-3 py-3 font-sans text-sm leading-relaxed text-osd placeholder:text-readout focus:border-caution"
        />
        <button
          type="submit"
          disabled={phase === "submitting" || phase === "polling"}
          className="min-h-11 bg-caution px-4 py-2 font-display text-sm font-bold uppercase tracking-wide text-letterbox disabled:opacity-50"
        >
          {phase === "submitting" || phase === "polling" ? "Research in progress" : "Run research"}
        </button>
      </form>

      {phase === "submitting" && <p className="mt-6 font-mono text-xs uppercase tracking-widest text-caution">Submitting</p>}
      {error && (
        <p role="alert" className="mt-6 border border-down px-3 py-3 font-sans text-sm text-down">
          {error}
        </p>
      )}
      {model && <Result model={model} polling={phase === "polling"} />}
    </main>
  );
}

function Result({ model, polling }: { model: ResearchDeskModel; polling: boolean }) {
  return (
    <article className="mt-10 space-y-8">
      <section>
        <h2 className="font-display text-lg uppercase tracking-wide text-osd">Research query</h2>
        <p className="mt-3 font-sans text-base leading-relaxed text-osd">{model.query}</p>
        <p className="mt-3 font-mono text-xs uppercase tracking-widest text-caution">
          {polling ? model.statusLabel : model.statusLabel}
          {model.researchId ? ` · ${model.researchId}` : ""}
        </p>
        {model.planSource && (
          <p className="mt-2 font-mono text-xs uppercase tracking-widest text-readout">Plan source {model.planSource}</p>
        )}
      </section>

      {model.terminal && model.summary.length > 0 && (
        <section>
          <h2 className="font-display text-lg uppercase tracking-wide text-osd">Executive summary</h2>
          <ul className="mt-4 space-y-3">
            {model.summary.map((item) => (
              <li key={item.statement} className="font-sans text-sm leading-relaxed text-osd">
                {item.statement}
              </li>
            ))}
          </ul>
        </section>
      )}

      {model.terminal && (
        <section>
          <h2 className="font-display text-lg uppercase tracking-wide text-osd">Key findings</h2>
          {model.findings.length === 0 ? (
            <p className="mt-4 font-sans text-sm text-readout">No verified findings were returned.</p>
          ) : (
            <ul className="mt-4 space-y-4">
              {model.findings.map((item) => (
                <Finding key={`${item.findingType}-${item.statement}`} item={item} citations={model.citations} />
              ))}
            </ul>
          )}
        </section>
      )}

      {model.citations.some((item) => item.market) && (
        <section>
          <h2 className="font-display text-lg uppercase tracking-wide text-osd">Market windows</h2>
          <ul className="mt-4 space-y-4">
            {model.citations.filter((item) => item.market).map((item) => (
              <MarketCard key={item.citationId || item.title} citation={item} />
            ))}
          </ul>
        </section>
      )}

      {model.excluded.length > 0 && (
        <section>
          <h2 className="font-display text-lg uppercase tracking-wide text-osd">Excluded from factual sections</h2>
          <ul className="mt-4 space-y-3">
            {model.excluded.map((item) => (
              <li key={item.statement} className="border border-hud bg-panel px-3 py-3">
                <Stamp type={item.findingType} verification={item.verification} />
                <p className="mt-2 font-sans text-sm leading-relaxed text-readout">{item.statement}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {model.terminal && (
        <section>
          <h2 className="font-display text-lg uppercase tracking-wide text-osd">Limitations</h2>
          {model.limitations.length === 0 ? (
            <p className="mt-3 font-sans text-sm text-readout">No additional limitations were recorded.</p>
          ) : (
            <ul className="mt-3 list-disc space-y-2 pl-5 font-sans text-sm leading-relaxed text-osd">
              {model.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {model.agents.length > 0 && (
        <section>
          <h2 className="font-display text-lg uppercase tracking-wide text-osd">Agent runs</h2>
          <ul className="mt-3 space-y-1 font-mono text-xs uppercase tracking-widest text-readout">
            {model.agents.map((agent) => (
              <li key={`${agent.agent}-${agent.attempt}`}>
                {agent.agent} {agent.status}
                {agent.attempt ? ` attempt ${agent.attempt}` : ""}
                {agent.error ? ` ${agent.error}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {model.error && <p className="font-sans text-sm text-down">{model.error}</p>}
    </article>
  );
}

function Finding({ item, citations }: { item: FindingView; citations: CitationView[] }) {
  const linked = citations.filter((citation) => item.citationIds.includes(citation.citationId));
  return (
    <li className="border border-hud bg-panel px-3 py-4 sm:px-4">
      <Stamp type={item.findingType} verification={item.verification} />
      <p className="mt-3 font-sans text-sm leading-relaxed text-osd">{item.statement}</p>
      {linked.length > 0 && (
        <ul className="mt-3 space-y-2">
          {linked.map((citation, index) => (
            <Citation key={citation.citationId || `${index}`} citation={citation} index={index + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

function Citation({ citation, index }: { citation: CitationView; index: number }) {
  return (
    <li className="font-sans text-xs leading-relaxed text-readout">
      <span className="text-osd">[{index}] </span>
      {citation.kind === "market_fact" ? marketLabel(citation) : citation.title}
      {citation.publisher ? ` · ${citation.publisher}` : ""}
      {citation.publishedAt ? ` · ${citation.publishedAt.slice(0, 10)}` : ""}
      {citation.href ? (
        <>
          {" "}
          <a href={citation.href} target="_blank" rel="noreferrer" className="text-cyan underline-offset-2 hover:underline focus:underline">
            Open source
          </a>
        </>
      ) : (
        <span> · Source URL unavailable</span>
      )}
    </li>
  );
}

function marketLabel(citation: CitationView): string {
  const market = citation.market;
  if (!market?.verified) return MARKET_IDENTITY_UNAVAILABLE;
  return market.displayName ? `${market.displayName} (${market.symbol})` : market.symbol;
}

function MarketCard({ citation }: { citation: CitationView }) {
  const market = citation.market;
  if (!market) return null;
  if (!market.verified) {
    return (
      <li className="border border-hud bg-panel px-3 py-4">
        <p className="font-sans text-sm text-osd">{MARKET_IDENTITY_UNAVAILABLE}</p>
      </li>
    );
  }
  const heading = market.displayName ? `${market.displayName} (${market.symbol})` : market.symbol;
  return (
    <li className="overflow-x-auto border border-hud bg-panel px-3 py-4">
      <p className="font-mono text-xs uppercase tracking-widest text-cyan">{heading}</p>
      <p className="mt-2 font-sans text-sm text-osd">Relative return vs {market.benchmark}</p>
      <p className="mt-1 font-sans text-xs text-readout">{market.method}</p>
      <table className="mt-3 w-full min-w-[28rem] text-left font-mono text-xs text-osd">
        <thead>
          <tr className="text-readout">
            <th className="py-1 pr-3 font-normal">Window</th>
            <th className="py-1 pr-3 font-normal">Return</th>
            <th className="py-1 pr-3 font-normal">Benchmark</th>
            <th className="py-1 font-normal">Relative return</th>
          </tr>
        </thead>
        <tbody>
          {market.windows.map((window) => (
            <tr key={window.label} className="border-t border-hud">
              <td className="py-1 pr-3">{window.label}</td>
              <td className="py-1 pr-3">{window.assetReturn}</td>
              <td className="py-1 pr-3">{window.benchmarkReturn}</td>
              <td className="py-1">{window.relativeReturn}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 font-mono text-xs text-readout">
        {market.volumeRatio ? `Volume ratio ${market.volumeRatio}` : "Volume ratio unavailable"}
        {market.volatility ? ` · Window volatility ${market.volatility}` : ""}
        {market.eventTradingDate ? ` · T0 ${market.eventTradingDate}` : ""}
      </p>
    </li>
  );
}

function Stamp({ type, verification }: { type: string; verification: string }) {
  const typeClass =
    type === "FACT"
      ? "text-caution"
      : type === "OBSERVATION"
        ? "text-cyan"
        : type === "INFERENCE"
          ? "text-magenta"
          : "text-readout";
  const verifyClass =
    verification === "supported"
      ? "text-caution"
      : verification === "uncertain"
        ? "text-magenta"
        : "text-down";
  return (
    <p className="font-mono text-xs uppercase tracking-widest">
      <span className={typeClass}>{type}</span>
      <span className="text-readout"> · </span>
      <span className={verifyClass}>{verification}</span>
    </p>
  );
}
