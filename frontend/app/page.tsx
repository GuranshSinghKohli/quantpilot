"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AgentWorkflow from "@/components/AgentWorkflow";
import AppFooter from "@/components/AppFooter";
import AppHeader from "@/components/AppHeader";
import AppRail from "@/components/AppRail";
import EditionMarquee from "@/components/motion/EditionMarquee";
import PageEnter from "@/components/motion/PageEnter";
import CitationsPanel from "@/components/CitationsPanel";
import DebatePanel from "@/components/DebatePanel";
import MoveHeadline from "@/components/MoveHeadline";
import CaseHistory from "@/components/case/CaseHistory";
import CausesColumn from "@/components/case/CausesColumn";
import HowItWorks from "@/components/case/HowItWorks";
import ReceiptsColumn from "@/components/case/ReceiptsColumn";
import { useCase } from "@/components/case/useCase";
import InvestigationChatPanel from "@/components/InvestigationChatPanel";
import {
  Bracketed,
  Caution,
  ColumnHeader,
  WindowBar,
} from "@/components/hud/primitives";
import { useLenis } from "lenis/react";
import LoadingPipeline from "@/components/LoadingPipeline";
import InvestmentMemoPanel from "@/components/InvestmentMemoPanel";
import ReportDisplay from "@/components/ReportDisplay";
import ResearchExtrasPanel from "@/components/ResearchExtrasPanel";
import ResearchChatPanel from "@/components/ResearchChatPanel";
import SearchBar, {
  INVESTIGATE_WINDOWS,
  QUICK_PICKS,
  type InvestigateWindow,
} from "@/components/SearchBar";
import SECFilingsPanel from "@/components/SECFilingsPanel";
import ToolsDock, { type ToolsTab } from "@/components/ToolsDock";
import {
  addToWatchlist,
  ApiError,
  checkApiHealth,
  investigateTicker,
  fetchAnalysisWithFallback,
  fetchHistory,
  fetchInvestigations,
  fetchPastReports,
  fetchStockData,
  fetchWatchlist,
  removeFromWatchlist,
} from "@/lib/api";
import { EXAMPLE_CASE, EXAMPLE_CASE_ID } from "@/lib/exampleCase";
import type {
  AgentStep,
  AnalysisResponse,
  AuthUser,
  HistoryEntry,
  StockData,
  WatchlistEntry,
} from "@/types";

const INITIAL_STEPS: AgentStep[] = [
  { id: "news", name: "News Agent", status: "waiting" },
  { id: "financial", name: "Financial Agent", status: "waiting" },
  { id: "sec", name: "SEC Agent", status: "waiting" },
  { id: "earnings", name: "Earnings Agent", status: "waiting" },
  { id: "macro", name: "Macro Agent", status: "waiting" },
  { id: "risk", name: "Risk Agent", status: "waiting" },
  { id: "bull", name: "Bull Agent", status: "waiting" },
  { id: "bear", name: "Bear Agent", status: "waiting" },
  { id: "verification", name: "Verification Agent", status: "waiting" },
  { id: "report", name: "Report Agent", status: "waiting" },
  { id: "memo", name: "Memo Agent", status: "waiting" },
];

const AGENT_INDEX: Record<string, number> = {
  news: 0,
  financial: 1,
  sec: 2,
  earnings: 3,
  macro: 4,
  risk: 5,
  bull: 6,
  bear: 7,
  verification: 8,
  report: 9,
  memo: 10,
};

export default function Home() {
  const [currentTicker, setCurrentTicker] = useState<string | null>(null);
  const [stockData, setStockData] = useState<StockData | null>(null);
  const [analysisReport, setAnalysisReport] = useState<AnalysisResponse | null>(
    null
  );
  const [isLoading, setIsLoading] = useState(false);
  const [agentSteps, setAgentSteps] = useState<AgentStep[]>(INITIAL_STEPS);
  const [error, setError] = useState<string | null>(null);
  const [recentTickers, setRecentTickers] = useState<string[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [analyzedAt, setAnalyzedAt] = useState<string | undefined>();
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [activeInvestigationId, setActiveInvestigationId] = useState<
    number | null
  >(null);
  const [isOpeningInvestigation, setIsOpeningInvestigation] = useState(false);
  const [investigateWindow, setInvestigateWindow] =
    useState<InvestigateWindow>("1d");
  const [ledgerKey, setLedgerKey] = useState(0);
  const [toolsFocusRequest, setToolsFocusRequest] = useState<{
    tab: ToolsTab;
    key: number;
  } | null>(null);

  const { detail: caseDetail, error: caseError } = useCase(
    activeInvestigationId
  );
  const lenis = useLenis();

  useEffect(() => {
    const tool = new URLSearchParams(window.location.search).get("tool");
    if (
      tool === "watchlist" ||
      tool === "portfolio" ||
      tool === "briefing" ||
      tool === "alerts" ||
      tool === "ideas" ||
      tool === "history"
    ) {
      setToolsFocusRequest({ tab: tool, key: Date.now() });
    }
  }, []);

  const hasCase = activeInvestigationId != null;
  const isExampleCase = activeInvestigationId === EXAMPLE_CASE_ID;
  const currentAgentStep = agentSteps.find((step) => step.status === "running");
  const completedAgentCount = agentSteps.filter(
    (step) => step.status === "complete"
  ).length;
  const isOnWatchlist = Boolean(
    currentTicker &&
      watchlist.some(
        (e) => e.ticker.toUpperCase() === currentTicker.toUpperCase()
      )
  );
  const caseBusy =
    isOpeningInvestigation ||
    (caseDetail != null &&
      !["complete", "failed", "skipped_market_noise"].includes(
        String(caseDetail.status)
      ));

  /* Zebra means one thing only: something here is unverified. A case that was
     merely challenged still rests on evidence, so it gets the amber note. */
  const unverifiedClaims = (caseDetail?.claims ?? []).filter(
    (claim) => claim.evidence_links.length === 0
  ).length;
  const caseFlag = caseBusy
    ? null
    : caseDetail?.status === "failed"
      ? "This case could not be settled — treat nothing below as verified."
      : unverifiedClaims > 0
        ? `${unverifiedClaims} of ${caseDetail?.claims?.length ?? 0} explanations below carry no evidence yet.`
        : null;
  const caseNote = caseFlag
    ? null
    : caseDetail?.da_outcome?.reversal
      ? "The Devil's Advocate pass demoted the leading explanation."
      : caseDetail?.da_outcome?.leading_weakened
        ? "The Devil's Advocate pass weakened the leading explanation."
        : null;

  /* Nobody should meet three empty columns. On arrival the newest real case
     opens; with no history at all, a saved example stands in so the page
     teaches itself before the visitor types anything. */
  useEffect(() => {
    let cancelled = false;

    async function openSomething() {
      let opened: { id: number; ticker: string } | null = null;
      try {
        const [recent] = await fetchInvestigations(1);
        if (recent) {
          opened = { id: recent.id, ticker: recent.ticker };
          setInvestigateWindow(
            (recent.window_label || "1d") as InvestigateWindow
          );
        }
      } catch {
        /* no history reachable; the example still has us covered */
      }
      if (cancelled) return;
      const target = opened ?? {
        id: EXAMPLE_CASE_ID,
        ticker: EXAMPLE_CASE.ticker,
      };
      setActiveInvestigationId(target.id);
      setCurrentTicker(target.ticker);
      if (!opened) {
        setInvestigateWindow(EXAMPLE_CASE.window_label as InvestigateWindow);
      }
    }

    void openSomething();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function ping() {
      const ok = await checkApiHealth();
      if (!cancelled) setApiReachable(ok);
    }

    ping();
    const id = window.setInterval(ping, 20_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const refreshWatchlist = useCallback(async () => {
    try {
      setWatchlist(await fetchWatchlist());
    } catch {
      // watchlist fetch failed; keep local state
    }
  }, []);

  const refreshSidebar = useCallback(async () => {
    await refreshWatchlist();
    try {
      setHistory(await fetchHistory());
    } catch {
      // history optional (may fail without Chroma/OpenAI)
    }
  }, [refreshWatchlist]);

  useEffect(() => {
    refreshSidebar();
  }, [refreshSidebar, user]);

  useEffect(() => {
    if (!currentTicker) return;
    let cancelled = false;
    void fetchStockData(currentTicker)
      .then((quote) => {
        if (!cancelled) setStockData(quote);
      })
      .catch(() => {
        /* quote tape is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [currentTicker]);

  const handleUserChange = useCallback((next: AuthUser | null) => {
    setUser(next);
  }, []);

  const handleSourcesClick = useCallback(() => {
    if (lenis) lenis.scrollTo("#data-sources", { offset: -32 });
    else
      document.getElementById("data-sources")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
  }, [lenis]);

  const handlePositionClick = useCallback(() => {
    setToolsFocusRequest({ tab: "portfolio", key: Date.now() });
  }, []);

  function resetAgentSteps() {
    setAgentSteps(
      INITIAL_STEPS.map((s) => ({ ...s, status: "waiting" as const }))
    );
  }

  function markStepRunning(index: number) {
    setAgentSteps((prev) =>
      prev.map((step, i) => {
        if (i < index) return { ...step, status: "complete" };
        if (i === index) return { ...step, status: "running" };
        return { ...step, status: "waiting" };
      })
    );
  }

  function markAgentStarted(agentId: string) {
    const index = AGENT_INDEX[agentId];
    if (index !== undefined) markStepRunning(index);
  }

  function markAgentCompleted(agentId: string) {
    const index = AGENT_INDEX[agentId];
    if (index === undefined) return;
    setAgentSteps((prev) =>
      prev.map((step, i) => {
        if (i <= index) return { ...step, status: "complete" };
        if (i === index + 1) return { ...step, status: "running" };
        return step;
      })
    );
  }

  function markAllComplete() {
    setAgentSteps((prev) =>
      prev.map((step) => ({ ...step, status: "complete" }))
    );
  }

  const handleCaseSelect = useCallback((id: number, ticker?: string) => {
    setActiveInvestigationId(id);
    if (!ticker) return;
    const symbol = ticker.toUpperCase();
    setCurrentTicker(symbol);
    setRecentTickers((prev) =>
      [symbol, ...prev.filter((t) => t !== symbol)].slice(0, 8)
    );
    void fetchStockData(symbol)
      .then(setStockData)
      .catch(() => {
        /* quote is optional */
      });
  }, []);

  /* Changing the look-back has to re-open the case. Relabelling the headline
     while the old figures stay put would make the readout lie. */
  function handleWindowChange(next: InvestigateWindow) {
    setInvestigateWindow(next);
    if (next === investigateWindow) return;
    if (currentTicker && apiReachable !== false) {
      void runInvestigate(currentTicker, next);
    }
  }

  async function runInvestigate(
    ticker: string,
    windowLabel: InvestigateWindow | string = "1d"
  ) {
    const symbol = ticker.toUpperCase();
    const resolvedWindow: InvestigateWindow = (
      ["1d", "1w", "1mo", "6mo", "1y"] as const
    ).includes(windowLabel as InvestigateWindow)
      ? (windowLabel as InvestigateWindow)
      : "1d";

    setInvestigateWindow(resolvedWindow);
    setError(null);
    setCurrentTicker(symbol);
    setStockData((prev) =>
      prev?.ticker?.toUpperCase() === symbol ? prev : null
    );
    setRecentTickers((prev) =>
      [symbol, ...prev.filter((t) => t !== symbol)].slice(0, 8)
    );

    const reachable = apiReachable ?? (await checkApiHealth());
    setApiReachable(reachable);
    if (!reachable) return;

    setIsOpeningInvestigation(true);
    void fetchStockData(symbol)
      .then(setStockData)
      .catch(() => {
        /* quote is optional while the case opens */
      });

    try {
      const detail = await investigateTicker({
        ticker: symbol,
        trigger_reason: "on_demand",
        window_label: resolvedWindow,
        skip_if_noise: false,
      });
      setActiveInvestigationId(detail.id);
      setLedgerKey((k) => k + 1);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not open a case for this ticker. Try again in a moment."
      );
    } finally {
      setIsOpeningInvestigation(false);
    }
  }

  async function runAnalysis(ticker: string) {
    const symbol = ticker.toUpperCase();
    setCurrentTicker(symbol);
    setError(null);
    setStockData(null);
    setAnalysisReport(null);

    const reachable = apiReachable ?? (await checkApiHealth());
    setApiReachable(reachable);
    if (!reachable) {
      setIsLoading(false);
      resetAgentSteps();
      return;
    }

    setIsLoading(true);
    resetAgentSteps();
    setRecentTickers((prev) =>
      [symbol, ...prev.filter((t) => t !== symbol)].slice(0, 8)
    );

    try {
      setStockData(await fetchStockData(symbol));
      const analysis = await fetchAnalysisWithFallback(symbol, {
        onAgentStarted: markAgentStarted,
        onAgentCompleted: markAgentCompleted,
      });
      markAllComplete();
      setAnalysisReport(analysis);
      setAnalyzedAt(new Date().toISOString());
      await refreshSidebar();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "The research report failed. Try again in a moment."
      );
      resetAgentSteps();
      checkApiHealth().then(setApiReachable);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleAddWatchlist() {
    if (!currentTicker) {
      setError("Frame a ticker first, then track it.");
      return;
    }
    if (isOnWatchlist) {
      setNotice(`${currentTicker} is already tracked.`);
      setTimeout(() => setNotice(null), 3000);
      return;
    }

    setWatchlistLoading(true);
    setNotice(null);
    setError(null);
    try {
      const entry = await addToWatchlist(currentTicker);
      setWatchlist((prev) =>
        prev.some((e) => e.ticker.toUpperCase() === entry.ticker.toUpperCase())
          ? prev
          : [entry, ...prev]
      );
      setNotice(`${entry.ticker} is now tracked.`);
      setTimeout(() => setNotice(null), 4000);
      await refreshWatchlist();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not track that ticker."
      );
    } finally {
      setWatchlistLoading(false);
    }
  }

  async function handleRemoveWatchlist(ticker: string) {
    try {
      await removeFromWatchlist(ticker);
      await refreshSidebar();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not untrack that ticker."
      );
    }
  }

  async function handleHistorySelect(ticker: string) {
    setHistoryLoading(true);
    setError(null);
    try {
      const reports = await fetchPastReports(ticker);
      if (reports.length > 0 && reports[0].report) {
        setCurrentTicker(ticker);
        setAnalysisReport({
          ticker,
          final_report: reports[0].report,
          risk_output: reports[0].metadata?.risk_level
            ? {
                risk_level: reports[0].metadata.risk_level as
                  | "LOW"
                  | "MEDIUM"
                  | "HIGH",
                risk_factors: [],
                confidence_score: 0.5,
              }
            : undefined,
        });
        const stock = await fetchStockData(ticker).catch(() => null);
        if (stock) setStockData(stock);
        markAllComplete();
      } else {
        await runAnalysis(ticker);
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not load that past report."
      );
    } finally {
      setHistoryLoading(false);
    }
  }

  return (
    <PageEnter>
      <div className="relative flex min-h-screen flex-col pb-28 sm:pb-32">
      <AppHeader
        apiReachable={apiReachable}
        user={user}
        onUserChange={handleUserChange}
        sourcesAvailable={Boolean(analysisReport && !isLoading)}
        onSourcesClick={handleSourcesClick}
        onPortfolioClick={handlePositionClick}
        caseId={activeInvestigationId}
        search={
          <SearchBar
            onInvestigate={runInvestigate}
            onDeepResearch={runAnalysis}
            isLoading={isLoading || isOpeningInvestigation}
            disabled={apiReachable === false}
            windowLabel={investigateWindow}
            loadingLabel={isLoading ? "Researching…" : "Framing…"}
          />
        }
      />

      <AppRail
        onTool={(tab) =>
          setToolsFocusRequest({ tab, key: Date.now() })
        }
      />

      <EditionMarquee
        tickers={recentTickers.length > 0 ? recentTickers : QUICK_PICKS}
        onPick={(t) => void runInvestigate(t, investigateWindow)}
      />

      <MoveHeadline
        ticker={currentTicker}
        quote={stockData}
        movePct={caseDetail?.move_pct ?? null}
        /* The window the open case actually measured, not the one being picked. */
        windowLabel={caseDetail?.window_label || investigateWindow}
        triggerReason={caseDetail?.trigger_reason}
        status={caseDetail?.status}
        busy={caseBusy}
        flagged={caseFlag != null}
        flagNote={caseFlag}
        note={caseNote}
        isExample={isExampleCase}
        windowControl={
          <WindowBar
            name="lookback"
            options={INVESTIGATE_WINDOWS}
            value={investigateWindow}
            onChange={handleWindowChange}
            disabled={isLoading || isOpeningInvestigation}
          />
        }
      />

      <main className="w-full flex-1 px-3 py-3 sm:px-4">
        {(apiReachable === false || error || caseError || notice) && (
          <div className="mb-5 space-y-2.5">
            {apiReachable === false && (
              <Caution title="QuantPilot cannot reach its research service">
                New cases are paused until it answers. Nothing you saved is
                lost. Reload in a minute and it usually comes back.
                <span className="mt-1.5 block text-readout">
                  Running this yourself? Start the backend, then check{" "}
                  <code className="text-caution">NEXT_PUBLIC_API_URL</code> and{" "}
                  <code className="text-caution">ALLOWED_ORIGINS</code>.
                </span>
              </Caution>
            )}
            {error && apiReachable !== false && (
              <Caution title="Something failed">{error}</Caution>
            )}
            {caseError && <Caution title="Case unavailable">{caseError}</Caution>}
            {notice && (
              <p role="status" className="text-sm text-osd/85">
                {notice}
              </p>
            )}
          </div>
        )}

        <div className="grid items-stretch gap-3 md:grid-cols-2">
          <CausesColumn
            claims={caseDetail?.claims ?? []}
            summary={caseDetail?.summary}
            ticker={caseDetail?.ticker ?? currentTicker ?? undefined}
            daOutcome={caseDetail?.da_outcome}
            busy={caseBusy}
            hasCase={hasCase}
            quickPicks={
              recentTickers.length > 0 ? recentTickers : QUICK_PICKS
            }
            onQuickPick={(t) => void runInvestigate(t, investigateWindow)}
          />
          <ReceiptsColumn
            evidence={caseDetail?.evidence_items ?? []}
            claims={caseDetail?.claims ?? []}
            busy={caseBusy}
            hasCase={hasCase}
          />
        </div>

        {hasCase && currentTicker && activeInvestigationId != null && (
          <div className="mt-3 flex flex-wrap gap-4 border border-hud px-3 py-3">
            <Link
              href={`/investigations/${activeInvestigationId}`}
              className="font-mono text-sm uppercase tracking-osd text-caution hover:underline"
            >
              Full investigation
            </Link>
            <Link
              href={`/companies/${encodeURIComponent(currentTicker)}`}
              className="font-mono text-sm uppercase tracking-osd text-readout hover:text-caution"
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
        )}

        {hasCase && currentTicker && activeInvestigationId != null && (
          <div className="mt-3">
            <InvestigationChatPanel
              investigationId={activeInvestigationId}
              ticker={currentTicker}
            />
          </div>
        )}

        <div className="mt-3">
          <HowItWorks />
        </div>

        <div className="mt-3">
          <CaseHistory
            selectedId={activeInvestigationId}
            onSelect={handleCaseSelect}
            refreshKey={ledgerKey}
          />
        </div>

        <div className="mt-4">
          <ToolsDock
            user={user}
            watchlist={watchlist}
            history={history}
            currentTicker={currentTicker}
            watchlistLoading={watchlistLoading}
            historyLoading={historyLoading}
            isLoadingAnalysis={isLoading}
            onSelectTicker={runInvestigate}
            onOpenInvestigation={(id, ticker) => handleCaseSelect(id, ticker)}
            onHistorySelect={handleHistorySelect}
            onAddWatchlist={handleAddWatchlist}
            onRemoveWatchlist={handleRemoveWatchlist}
            focusRequest={toolsFocusRequest}
          />
        </div>

        {(isLoading || analysisReport) && (
          <div className="mt-4 space-y-4">
            <Bracketed as="section" large className="p-4 sm:p-5">
              <ColumnHeader
                aside={`${completedAgentCount}/${agentSteps.length}`}
              >
                Deep research
              </ColumnHeader>
              <AgentWorkflow
                steps={agentSteps}
                isLoading={isLoading}
                collapsed={!isLoading && !!analysisReport}
              />
            </Bracketed>

            {isLoading && (
              <LoadingPipeline
                ticker={currentTicker}
                currentStep={currentAgentStep}
                completedCount={completedAgentCount}
                totalCount={agentSteps.length}
              />
            )}

            {analysisReport && !isLoading && (
              <>
                {analysisReport.investment_memo && (
                  <InvestmentMemoPanel memo={analysisReport.investment_memo} />
                )}

                <ReportDisplay
                  analysis={analysisReport}
                  omitOverlappingSummary={Boolean(
                    analysisReport.investment_memo
                  )}
                />

                {analysisReport.debate_output && (
                  <DebatePanel debate={analysisReport.debate_output} />
                )}

                <ResearchExtrasPanel analysis={analysisReport} />

                {currentTicker && (
                  <SECFilingsPanel
                    ticker={currentTicker}
                    secOutput={analysisReport.sec_output}
                  />
                )}

                <CitationsPanel
                  analysis={analysisReport}
                  analyzedAt={analyzedAt}
                />

                {currentTicker && (
                  <ResearchChatPanel
                    ticker={currentTicker}
                    analysis={analysisReport}
                  />
                )}
              </>
            )}
          </div>
        )}
      </main>

      <AppFooter
        caseId={activeInvestigationId}
        windowLabel={investigateWindow}
      />
    </div>
    </PageEnter>
  );
}
