"use client";

import { useCallback, useEffect, useState } from "react";
import AgentWorkflow from "@/components/AgentWorkflow";
import AppFooter from "@/components/AppFooter";
import AppHeader from "@/components/AppHeader";
import CitationsPanel from "@/components/CitationsPanel";
import DebatePanel from "@/components/DebatePanel";
import FeatureCards from "@/components/FeatureCards";
import HeroSection from "@/components/HeroSection";
import LoadingPipeline from "@/components/LoadingPipeline";
import InvestmentMemoPanel from "@/components/InvestmentMemoPanel";
import ReportDisplay from "@/components/ReportDisplay";
import ResearchExtrasPanel from "@/components/ResearchExtrasPanel";
import ResearchChatPanel from "@/components/ResearchChatPanel";
import SearchBar from "@/components/SearchBar";
import SECFilingsPanel from "@/components/SECFilingsPanel";
import StockHeader from "@/components/StockHeader";
import ToolsDock from "@/components/ToolsDock";
import {
  addToWatchlist,
  ApiError,
  checkApiHealth,
  fetchAnalysisWithFallback,
  fetchHistory,
  fetchPastReports,
  fetchStockData,
  fetchWatchlist,
  removeFromWatchlist,
} from "@/lib/api";
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
  const [analysisReport, setAnalysisReport] = useState<AnalysisResponse | null>(null);
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
  const [watchlistSuccess, setWatchlistSuccess] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);

  const hasResults = Boolean(stockData || analysisReport || isLoading);
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
      const wl = await fetchWatchlist();
      setWatchlist(wl);
    } catch {
      // watchlist fetch failed; keep local state
    }
  }, []);

  const refreshSidebar = useCallback(async () => {
    await refreshWatchlist();
    try {
      const hist = await fetchHistory();
      setHistory(hist);
    } catch {
      // history optional (may fail without Chroma/OpenAI)
    }
  }, [refreshWatchlist]);

  useEffect(() => {
    refreshSidebar();
  }, [refreshSidebar, user]);

  const handleUserChange = useCallback((next: AuthUser | null) => {
    setUser(next);
  }, []);

  const handleSourcesClick = useCallback(() => {
    document.getElementById("data-sources")?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, []);

  function resetAgentSteps() {
    setAgentSteps(INITIAL_STEPS.map((s) => ({ ...s, status: "waiting" as const })));
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

  async function runAnalysis(ticker: string) {
    const symbol = ticker.toUpperCase();
    setCurrentTicker(symbol);
    setError(null);
    setStockData(null);
    setAnalysisReport(null);

    // Don't spin forever when the API is already known offline (common on
    // Vercel when NEXT_PUBLIC_API_URL points at a dead Railway service).
    const reachable = apiReachable ?? (await checkApiHealth());
    setApiReachable(reachable);
    if (!reachable) {
      setIsLoading(false);
      resetAgentSteps();
      setError(
        "Backend API is unreachable. For local use open http://localhost:3000 with the API on :8000. On Vercel, set NEXT_PUBLIC_API_URL to your live Railway URL and redeploy."
      );
      return;
    }

    setIsLoading(true);
    resetAgentSteps();

    setRecentTickers((prev) => {
      const next = [symbol, ...prev.filter((t) => t !== symbol)];
      return next.slice(0, 8);
    });

    try {
      const stock = await fetchStockData(symbol);
      setStockData(stock);

      const analysis = await fetchAnalysisWithFallback(symbol, {
        onAgentStarted: markAgentStarted,
        onAgentCompleted: markAgentCompleted,
      });
      markAllComplete();
      setAnalysisReport(analysis);
      setAnalyzedAt(new Date().toISOString());
      await refreshSidebar();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Analysis failed. Please try again.";
      setError(message);
      resetAgentSteps();
      // Re-check connectivity after a failed run.
      checkApiHealth().then(setApiReachable);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleAddWatchlist() {
    if (!currentTicker) {
      setError("Run an analysis first, then add the ticker to your watchlist.");
      return;
    }

    if (isOnWatchlist) {
      setWatchlistSuccess(`${currentTicker} is already on your watchlist.`);
      setTimeout(() => setWatchlistSuccess(null), 3000);
      return;
    }

    setWatchlistLoading(true);
    setWatchlistSuccess(null);
    setError(null);
    try {
      const entry = await addToWatchlist(currentTicker);
      setWatchlist((prev) => {
        const exists = prev.some(
          (e) => e.ticker.toUpperCase() === entry.ticker.toUpperCase()
        );
        if (exists) return prev;
        return [entry, ...prev];
      });
      setWatchlistSuccess(`${entry.ticker} added to your watchlist.`);
      setTimeout(() => setWatchlistSuccess(null), 4000);
      await refreshWatchlist();
      try {
        const hist = await fetchHistory();
        setHistory(hist);
      } catch {
        // non-blocking
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add to watchlist");
    } finally {
      setWatchlistLoading(false);
    }
  }

  async function handleRemoveWatchlist(ticker: string) {
    try {
      await removeFromWatchlist(ticker);
      await refreshSidebar();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove from watchlist");
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
                risk_level: reports[0].metadata.risk_level as "LOW" | "MEDIUM" | "HIGH",
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
      setError(err instanceof ApiError ? err.message : "Could not load past report");
    } finally {
      setHistoryLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen flex-col">
      <div
        className="pointer-events-none fixed inset-0 overflow-hidden"
        aria-hidden
      >
        <div className="absolute -left-32 top-1/4 h-96 w-96 rounded-full bg-violet-600/8 blur-3xl" />
        <div className="absolute -right-32 top-1/3 h-80 w-80 rounded-full bg-cyan-500/6 blur-3xl" />
      </div>

      <AppHeader
        apiReachable={apiReachable}
        user={user}
        onUserChange={handleUserChange}
        sourcesAvailable={Boolean(analysisReport && !isLoading)}
        onSourcesClick={handleSourcesClick}
      />

      <main className="relative mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <HeroSection compact={hasResults} />

        <section className="mt-6 sm:mt-8" aria-label="Search">
          <SearchBar
            onAnalyze={runAnalysis}
            isLoading={isLoading}
            recentTickers={recentTickers}
            disabled={apiReachable === false}
            loadingLabel={
              currentTicker ? `Analyzing ${currentTicker}…` : "Analyzing…"
            }
          />
        </section>

        {apiReachable === false && (
          <div
            className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200 sm:mt-6"
            role="alert"
          >
            <p className="font-medium text-amber-100">Backend API is offline</p>
            <p className="mt-1 text-amber-200/90">
              Local: run FastAPI on port 8000 and open{" "}
              <code className="rounded bg-black/30 px-1">http://localhost:3000</code>
              . Production: set{" "}
              <code className="rounded bg-black/30 px-1">NEXT_PUBLIC_API_URL</code>{" "}
              on Vercel to your Railway URL, add this site to backend{" "}
              <code className="rounded bg-black/30 px-1">ALLOWED_ORIGINS</code>, then
              redeploy.
            </p>
          </div>
        )}

        {watchlistSuccess && (
          <div
            className="mt-4 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300 sm:mt-6"
            role="status"
          >
            {watchlistSuccess}
          </div>
        )}

        {error && (
          <div
            className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300 sm:mt-6"
            role="alert"
          >
            {error}
          </div>
        )}

        <ToolsDock
          user={user}
          watchlist={watchlist}
          history={history}
          currentTicker={currentTicker}
          watchlistLoading={watchlistLoading}
          historyLoading={historyLoading}
          isLoadingAnalysis={isLoading}
          onSelectTicker={runAnalysis}
          onHistorySelect={handleHistorySelect}
          onAddWatchlist={handleAddWatchlist}
          onRemoveWatchlist={handleRemoveWatchlist}
        />

        <div className="mt-6 space-y-6 sm:mt-8 lg:space-y-8">
          {stockData && (
              <StockHeader
                stock={stockData}
                analysis={analysisReport}
                onAddToWatchlist={handleAddWatchlist}
                watchlistLoading={watchlistLoading}
                isOnWatchlist={isOnWatchlist}
              />
            )}

            {analysisReport && !isLoading && currentTicker && (
              <SECFilingsPanel
                ticker={currentTicker}
                secOutput={analysisReport.sec_output}
              />
            )}

            {(isLoading || analysisReport) && (
              <AgentWorkflow
                steps={agentSteps}
                isLoading={isLoading}
                collapsed={!isLoading && !!analysisReport}
              />
            )}

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
                {analysisReport.debate_output && (
                  <DebatePanel debate={analysisReport.debate_output} />
                )}
                <ResearchExtrasPanel analysis={analysisReport} />
                {analysisReport.investment_memo && (
                  <InvestmentMemoPanel memo={analysisReport.investment_memo} />
                )}
                <ReportDisplay analysis={analysisReport} />
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

            {!hasResults && !error && (
              <div className="space-y-6">
                <div className="gradient-border relative overflow-hidden px-6 py-12 text-center sm:py-16">
                  <div
                    className="pointer-events-none absolute left-1/2 top-8 h-24 w-24 -translate-x-1/2 rounded-full bg-violet-500/20 blur-2xl"
                    aria-hidden
                  />
                  <div className="relative mx-auto flex h-16 w-16 animate-float items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500/30 to-cyan-500/20 text-3xl shadow-glow">
                    📊
                  </div>
                  <p className="font-display relative mt-6 text-xl font-bold text-white sm:text-2xl">
                    pick a ticker, get the{" "}
                    <span className="gradient-text">full story</span>
                  </p>
                  <p className="relative mx-auto mt-3 max-w-md text-sm leading-relaxed text-slate-500">
                    Eleven AI agents stream live: news, fundamentals, SEC, earnings,
                    macro, risk, bull vs bear, verification, report, then a
                    shareable investment memo — with chat built in.
                  </p>
                  {watchlist.length > 0 && (
                    <div className="relative mt-6 flex flex-wrap justify-center gap-2">
                      <span className="w-full text-xs text-slate-600">
                        from your watchlist
                      </span>
                      {watchlist.slice(0, 4).map((e) => (
                        <button
                          key={e.ticker}
                          type="button"
                          onClick={() => runAnalysis(e.ticker)}
                          className="rounded-full border border-violet-500/30 bg-violet-500/10 px-4 py-1.5 text-sm font-medium text-violet-200 transition hover:bg-violet-500/20"
                        >
                          analyze ${e.ticker}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div>
                  <h2 className="panel-title mb-4">what you get</h2>
                  <FeatureCards />
                </div>
              </div>
            )}
        </div>
      </main>

      <AppFooter />
    </div>
  );
}
