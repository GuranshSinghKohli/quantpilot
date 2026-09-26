"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import ActivityFeed from "@/components/intelligence/ActivityFeed";
import AlertCard from "@/components/intelligence/AlertCard";
import DeskChrome from "@/components/intelligence/DeskChrome";
import {
  Bracketed,
  Caution,
  ColumnHeader,
  NoSignal,
  Readout,
} from "@/components/hud/primitives";
import { ApiError, fetchDashboard } from "@/lib/api";
import {
  formatMove,
  formatWhen,
  stageFromStatus,
} from "@/lib/investigationView";
import type { AlertEvent, DashboardResponse, InvestigationSummary } from "@/types";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const next = await fetchDashboard();
      setData(next);
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Dashboard is unavailable. The research service may be down."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function replaceAlert(next: AlertEvent) {
    setData((prev) =>
      prev
        ? {
            ...prev,
            alerts: prev.alerts.map((a) => (a.id === next.id ? next : a)),
          }
        : prev
    );
  }

  return (
    <DeskChrome>
      {loading && (
        <p role="status" className="osd-label">
          Loading desk intelligence…
        </p>
      )}
      {error && (
        <Caution title="Dashboard could not load">
          {error}
        </Caution>
      )}
      {!loading && !error && data && (
        <DashboardBody data={data} onAlertChange={replaceAlert} />
      )}
    </DeskChrome>
  );
}

function DashboardBody({
  data,
  onAlertChange,
}: {
  data: DashboardResponse;
  onAlertChange: (next: AlertEvent) => void;
}) {
  const { counts } = data;
  const todayLine =
    counts.investigations_today === 0
      ? "No material events investigated today."
      : `${counts.investigations_today} material event${
          counts.investigations_today === 1 ? "" : "s"
        } investigated today.`;

  return (
    <div className="space-y-6">
      <header>
        <p className="osd-label">Intelligence desk</p>
        <h1 className="osd-header mt-1">{todayLine}</h1>
        <p className="mt-2 text-sm text-readout">
          Counts come from stored investigations. Nothing here is invented for
          display. Systems project desk — Next.js + FastAPI + LangGraph — not a
          portfolio product.
        </p>
      </header>

      <div className="grid gap-4">
        <Bracketed as="section" className="p-4">
          <ColumnHeader>Research activity</ColumnHeader>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Count label="Investigations today" value={counts.investigations_today} />
            <Count label="Active" value={counts.active} />
            <Count label="Completed today" value={counts.completed_today} />
            <Count label="Skipped / suppressed" value={counts.skipped} />
          </div>
        </Bracketed>
      </div>

      <Bracketed as="section" id="alerts" className="p-4">
        <ColumnHeader aside={`${data.alerts.length} recent`}>
          Recent alerts
        </ColumnHeader>
        {data.alerts.length === 0 ? (
          <NoSignal
            title="No material events detected"
            detail="Autonomous alerts appear after a completed investigation meets the notify bar."
          />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {data.alerts.map((alert) => (
              <AlertCard key={alert.id} alert={alert} onChange={onAlertChange} />
            ))}
          </div>
        )}
      </Bracketed>

      <div className="grid gap-4 lg:grid-cols-2">
        <Bracketed as="section" className="p-4">
          <ColumnHeader>Active investigations</ColumnHeader>
          <InvestigationRows
            rows={data.active}
            emptyTitle="No active investigations"
            emptyDetail="QuantPilot is not collecting or verifying a case right now."
          />
        </Bracketed>
        <Bracketed as="section" className="p-4">
          <ColumnHeader>Recent investigations</ColumnHeader>
          <InvestigationRows
            rows={data.recent}
            emptyTitle="QuantPilot hasn't investigated anything yet"
            emptyDetail="Open a ticker on the desk or wait for the autonomous monitor."
          />
        </Bracketed>
      </div>

      <Bracketed as="section" className="p-4">
        <ColumnHeader>Recent company intelligence</ColumnHeader>
        {data.company_updates.length === 0 ? (
          <NoSignal
            title="Not enough historical intelligence yet"
            detail="Company themes appear after completed investigations write memory."
          />
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.company_updates.map((update) => (
              <li key={update.ticker} className="border border-hud p-3">
                <Link
                  href={`/companies/${update.ticker}`}
                  className="font-display text-lg font-bold uppercase text-caution"
                >
                  {update.ticker}
                </Link>
                {update.themes.length > 0 && (
                  <p className="mt-2 text-sm text-osd">
                    {update.themes.join(" · ")}
                  </p>
                )}
                {update.risks.length > 0 && (
                  <p className="mt-1 text-sm text-readout">
                    Risks: {update.risks.join(", ")}
                  </p>
                )}
                {update.management_trend && (
                  <p className="mt-1 text-sm text-osd/90">
                    {update.management_trend}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Bracketed>

      <Bracketed as="section" className="p-4">
        <ActivityFeed items={data.activity} />
      </Bracketed>
    </div>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="border border-hud px-3 py-2">
      <p className="osd-label">{label}</p>
      <p data-figures className="mt-1 font-mono text-xl text-osd">
        {value}
      </p>
    </div>
  );
}

function InvestigationRows({
  rows,
  emptyTitle,
  emptyDetail,
}: {
  rows: InvestigationSummary[];
  emptyTitle: string;
  emptyDetail: string;
}) {
  if (rows.length === 0) {
    return <NoSignal title={emptyTitle} detail={emptyDetail} />;
  }
  return (
    <ul className="divide-y divide-hud">
      {rows.map((row) => (
        <li key={row.id} className="py-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <Link
              href={`/investigations/${row.id}`}
              className="font-display text-lg font-bold uppercase text-caution"
            >
              {row.ticker}
            </Link>
            <Readout label={stageFromStatus(row.status)} />
          </div>
          <p className="mt-1 text-sm text-osd">
            {formatMove(row.move_pct)} · {row.summary || row.trigger_reason}
          </p>
          <p className="osd-label mt-1">{formatWhen(row.created_at)}</p>
        </li>
      ))}
    </ul>
  );
}
