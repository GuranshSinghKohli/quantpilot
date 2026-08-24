"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import AdvocatePanel from "@/components/intelligence/AdvocatePanel";
import ConclusionPanel from "@/components/intelligence/ConclusionPanel";
import DeskChrome from "@/components/intelligence/DeskChrome";
import EvidenceGraph from "@/components/intelligence/EvidenceGraph";
import EvidenceList from "@/components/intelligence/EvidenceList";
import HypothesisPanel from "@/components/intelligence/HypothesisPanel";
import VerificationPanel from "@/components/intelligence/VerificationPanel";
import WorkflowRail from "@/components/intelligence/WorkflowRail";
import CompanyTimeline from "@/components/intelligence/CompanyTimeline";
import {
  Bracketed,
  Caution,
  ColumnHeader,
  NoSignal,
  Readout,
} from "@/components/hud/primitives";
import { useCase } from "@/components/case/useCase";
import {
  asRecord,
  currentTask,
  formatMove,
  formatWhen,
  stageFromStatus,
  whyInvestigated,
  workflowFromCase,
} from "@/lib/investigationView";
import type { CompanyMemoryItem, InvestigationDetail } from "@/types";

export default function InvestigationPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const valid = Number.isFinite(id) && id > 0;
  const { detail, loading, error } = useCase(valid ? id : null);
  const inFlight =
    detail != null &&
    ["planning", "collecting", "verifying"].includes(String(detail.status));

  return (
    <DeskChrome caseId={valid ? id : null}>
      {!valid && (
        <Caution title="Invalid investigation">
          That address is not an investigation id.
        </Caution>
      )}
      {valid && loading && !detail && (
        <p role="status" className="osd-label">
          Loading investigation…
        </p>
      )}
      {valid && error && !detail && (
        <Caution title="Investigation not available">
          {error} You can only open cases from your own desk.
        </Caution>
      )}
      {valid && detail && (
        <article className="space-y-5">
          <header className="border-b border-hud pb-4">
            <p className="osd-label">Investigation</p>
            <div className="mt-1 flex flex-wrap items-end justify-between gap-3">
              <h1 className="osd-header">
                {detail.ticker} {formatMove(detail.move_pct)}
              </h1>
              <Readout
                label={stageFromStatus(detail.status)}
                tone={inFlight ? "caution" : "neutral"}
                live={inFlight}
              />
            </div>
            <p className="osd-label mt-2">
              Detected {formatWhen(detail.created_at)}
            </p>
            <p className="mt-3 text-sm text-osd/90">
              Why investigated: {whyInvestigated(detail).join(" · ") || "Stored trigger only."}
            </p>
            <div className="mt-3 flex flex-wrap gap-3">
              <Link
                href={`/companies/${encodeURIComponent(detail.ticker)}`}
                className="font-mono text-sm uppercase tracking-osd text-caution hover:underline"
              >
                Company intelligence
              </Link>
              <Link
                href="/dashboard"
                className="font-mono text-sm uppercase tracking-osd text-readout hover:text-caution"
              >
                Dashboard
              </Link>
            </div>
          </header>

          {inFlight && (
            <Caution title="Investigation in progress" role="status">
              Investigation is still running. Some evidence is not available yet.
            </Caution>
          )}
          {detail.status === "failed" && detail.error_message && (
            <Caution title="Investigation failed">{detail.error_message}</Caution>
          )}

          <WorkflowRail
            steps={workflowFromCase(detail)}
            currentTask={currentTask(detail)}
          />

          <Bracketed as="section" className="p-4">
            <ColumnHeader>Event</ColumnHeader>
            <dl className="grid gap-3 sm:grid-cols-3">
              <div>
                <dt className="osd-label">Ticker</dt>
                <dd className="text-sm text-osd">{detail.ticker}</dd>
              </div>
              <div>
                <dt className="osd-label">Move</dt>
                <dd data-figures className="text-sm text-osd">
                  {formatMove(detail.move_pct)}
                </dd>
              </div>
              <div>
                <dt className="osd-label">Trigger</dt>
                <dd className="text-sm text-osd">{detail.trigger_reason}</dd>
              </div>
            </dl>
          </Bracketed>

          <Bracketed as="section" className="p-4">
            <HypothesisPanel
              claims={detail.claims}
              evidence={detail.evidence_items}
            />
          </Bracketed>

          <Bracketed as="section" className="p-4">
            <EvidenceList
              evidence={detail.evidence_items}
              claims={detail.claims}
              incomplete={inFlight}
            />
          </Bracketed>

          <Bracketed as="section" className="p-4">
            <ColumnHeader brief="Explainability view of the leading hypothesis and its stored links.">
              Evidence relationships
            </ColumnHeader>
            <EvidenceGraph
              ticker={detail.ticker}
              movePct={detail.move_pct}
              claims={detail.claims}
              evidence={detail.evidence_items}
            />
          </Bracketed>

          <Bracketed as="section" className="p-4">
            <VerificationPanel detail={detail} />
          </Bracketed>

          <AdvocatePanel detail={detail} />

          <ConclusionPanel detail={detail} />

          <Bracketed as="section" className="p-4">
            <MemoryFromRoster detail={detail} />
          </Bracketed>
        </article>
      )}
    </DeskChrome>
  );
}

function memoryItemsFromRoster(raw: unknown): CompanyMemoryItem[] {
  const rec = asRecord(raw);
  const bags = [
    rec.items,
    rec.active_risks,
    rec.management_themes,
    rec.important_events,
    rec.key_claims,
  ];
  const out: CompanyMemoryItem[] = [];
  for (const bag of bags) {
    if (!Array.isArray(bag)) continue;
    for (const item of bag) {
      if (item && typeof item === "object" && "id" in item) {
        out.push(item as CompanyMemoryItem);
      }
    }
  }
  return out;
}

function MemoryFromRoster({ detail }: { detail: InvestigationDetail }) {
  const items = memoryItemsFromRoster(detail.roster?.company_memory);
  if (items.length === 0) {
    return (
      <NoSignal
        title="No company memory on this case"
        detail="Historical intelligence is written after synthesis. Open the company page after a completed investigation."
      />
    );
  }
  return <CompanyTimeline items={items} />;
}
