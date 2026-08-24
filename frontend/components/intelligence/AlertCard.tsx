"use client";

import Link from "next/link";
import { useState } from "react";
import { HudButton, Stamp } from "@/components/hud/primitives";
import { formatWhen } from "@/lib/investigationView";
import { markAlertRead, setAlertFeedback } from "@/lib/api";
import type { AlertEvent } from "@/types";

export default function AlertCard({
  alert,
  onChange,
}: {
  alert: AlertEvent;
  onChange?: (next: AlertEvent) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function feedback(value: "useful" | "not_useful" | "") {
    setBusy(true);
    try {
      const next = await setAlertFeedback(alert.id, value);
      onChange?.(next);
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    setBusy(true);
    try {
      const next = await markAlertRead(alert.id);
      onChange?.(next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="border border-hud p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-lg font-bold uppercase tracking-wide text-osd">
          {alert.ticker}
        </h3>
        <span className="osd-label">{formatWhen(alert.created_at)}</span>
      </div>
      <p className="mt-2 text-sm text-osd">{alert.title}</p>
      {alert.message && (
        <p className="mt-2 text-sm leading-relaxed text-osd/90">{alert.message}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {alert.materiality_score != null && (
          <Stamp tone="osd">Materiality {alert.materiality_score.toFixed(2)}</Stamp>
        )}
        {alert.is_read && <Stamp tone="osd">Read</Stamp>}
        {alert.feedback === "useful" && <Stamp>Useful</Stamp>}
        {alert.feedback === "not_useful" && <Stamp tone="osd">Not useful</Stamp>}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {alert.investigation_id && (
          <Link
            href={`/investigations/${alert.investigation_id}`}
            className="font-mono text-sm uppercase tracking-osd text-caution hover:underline"
          >
            View investigation
          </Link>
        )}
        <Link
          href={`/companies/${encodeURIComponent(alert.ticker)}`}
          className="font-mono text-sm uppercase tracking-osd text-readout hover:text-caution"
        >
          Company
        </Link>
        <HudButton
          variant="ghost"
          disabled={busy}
          onClick={() => void feedback(alert.feedback === "useful" ? "" : "useful")}
        >
          Useful
        </HudButton>
        <HudButton
          variant="ghost"
          disabled={busy}
          onClick={() =>
            void feedback(alert.feedback === "not_useful" ? "" : "not_useful")
          }
        >
          Not useful
        </HudButton>
        {!alert.is_read && (
          <HudButton variant="ghost" disabled={busy} onClick={() => void dismiss()}>
            Dismiss
          </HudButton>
        )}
      </div>
    </article>
  );
}
