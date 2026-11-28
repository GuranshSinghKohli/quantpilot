"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import CompanyTimeline from "@/components/intelligence/CompanyTimeline";
import DeskChrome from "@/components/intelligence/DeskChrome";
import {
  Bracketed,
  Caution,
  ColumnHeader,
  NoSignal,
  Readout,
} from "@/components/hud/primitives";
import { ApiError, fetchCompanyIntelligence } from "@/lib/api";
import { formatMove, formatWhen, stageFromStatus } from "@/lib/investigationView";
import type { CompanyIntelligence, CompanyMemoryItem } from "@/types";

export default function CompanyIntelligencePage() {
  const params = useParams<{ ticker: string }>();
  const ticker = String(params.ticker || "").toUpperCase();
  const [data, setData] = useState<CompanyIntelligence | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchCompanyIntelligence(ticker)
      .then((next) => {
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Company intelligence is unavailable."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  return (
    <DeskChrome>
      {loading && (
        <p role="status" className="osd-label">
          Loading company memory…
        </p>
      )}
      {error && <Caution title="Company intelligence failed">{error}</Caution>}
      {!loading && !error && data && <CompanyBody data={data} />}
    </DeskChrome>
  );
}

function CompanyBody({ data }: { data: CompanyIntelligence }) {
  const memory = data.memory;
  const trends = memory.management_themes || [];
  const showTrend = Boolean(data.management_trend) && trends.length >= 2;

  return (
    <div className="space-y-5">
      <header className="border-b border-hud pb-4">
        <p className="osd-label">Company intelligence</p>
        <h1 className="osd-header mt-1">{data.ticker}</h1>
        {memory.last_updated && (
          <p className="osd-label mt-2">Updated {formatWhen(memory.last_updated)}</p>
        )}
        <Link
          href="/dashboard"
          className="mt-3 inline-block font-mono text-sm uppercase tracking-osd text-readout hover:text-caution"
        >
          Dashboard
        </Link>
      </header>

      <Bracketed as="section" className="p-4">
        <ColumnHeader>Overview</ColumnHeader>
        {memory.key_facts.length === 0 &&
        memory.recent_themes.length === 0 &&
        data.investigations.length === 0 ? (
          <NoSignal
            title="Not enough historical intelligence yet"
            detail="Complete an investigation for this ticker to persist company memory."
          />
        ) : (
          <div className="space-y-3 text-sm text-osd">
            {memory.key_facts.length > 0 && (
              <ul className="list-disc pl-5">
                {memory.key_facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>
            )}
            {data.notes.map((note) => (
              <p key={note} className="text-readout">
                {note}
              </p>
            ))}
          </div>
        )}
      </Bracketed>

      <div className="grid gap-4 lg:grid-cols-2">
        <Bracketed as="section" className="p-4">
          <ColumnHeader>Historical investigations</ColumnHeader>
          {data.investigations.length === 0 ? (
            <NoSignal
              title="No stored investigations"
              detail="Owner-scoped cases for this ticker appear after they are opened."
            />
          ) : (
            <ul className="divide-y divide-hud">
              {data.investigations.map((row) => (
                <li key={row.id} className="py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <Link
                      href={`/investigations/${row.id}`}
                      className="text-sm text-caution hover:underline"
                    >
                      {formatWhen(row.created_at)}
                    </Link>
                    <Readout label={stageFromStatus(row.status)} />
                  </div>
                  <p className="mt-1 text-sm text-osd">
                    {formatMove(row.move_pct)} · {row.summary || row.trigger_reason}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Bracketed>

        <Bracketed as="section" className="p-4">
          <ColumnHeader>Recurring themes</ColumnHeader>
          {memory.recent_themes.length === 0 ? (
            <NoSignal title="No recurring themes" detail="Themes are extracted from stored memory." />
          ) : (
            <ul className="space-y-1 text-sm text-osd">
              {memory.recent_themes.map((theme) => (
                <li key={theme}>{theme}</li>
              ))}
            </ul>
          )}
        </Bracketed>
      </div>

      <Bracketed as="section" className="p-4">
        <ColumnHeader>Risks</ColumnHeader>
        {memory.recurring_risks.length === 0 && memory.active_risks.length === 0 ? (
          <NoSignal title="No stored risks" detail="Risks appear when investigations persist RISK memory." />
        ) : (
          <ul className="space-y-2 text-sm text-osd">
            {memory.recurring_risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
            {memory.active_risks.map((risk) => (
              <li key={risk.id}>{risk.statement}</li>
            ))}
          </ul>
        )}
      </Bracketed>

      <Bracketed as="section" className="p-4">
        <ColumnHeader brief="Shown only when enough historical management statements exist.">
          Management commentary
        </ColumnHeader>
        {!showTrend ? (
          <NoSignal
            title="Insufficient historical data"
            detail="A trend is withheld until at least two management statements are stored."
          />
        ) : (
          <div>
            <p className="text-sm text-osd">{data.management_trend}</p>
            <ul className="mt-3 space-y-2">
              {trends.map((item: CompanyMemoryItem) => (
                <li key={item.id} className="border border-hud p-3">
                  <p className="osd-label">
                    {(item.theme_key || "theme").replace(/_/g, " ")} · {item.polarity}
                  </p>
                  <p className="mt-1 text-sm text-osd">{item.statement}</p>
                  <p className="osd-label mt-1">{formatWhen(item.observed_at)}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Bracketed>

      <Bracketed as="section" className="p-4">
        <CompanyTimeline items={data.timeline} />
      </Bracketed>
    </div>
  );
}
