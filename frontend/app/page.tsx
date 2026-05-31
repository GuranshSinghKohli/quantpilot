"use client";

import { useCallback, useEffect, useState } from "react";
import AgentWorkflow from "@/components/AgentWorkflow";
import AppFooter from "@/components/AppFooter";
import AppHeader from "@/components/AppHeader";
import CitationsPanel from "@/components/CitationsPanel";
import FeatureCards from "@/components/FeatureCards";
import HeroSection from "@/components/HeroSection";
import HistoryPanel from "@/components/HistoryPanel";
import InvestmentIdeasPanel from "@/components/InvestmentIdeasPanel";
import LoadingPipeline from "@/components/LoadingPipeline";
import ReportDisplay from "@/components/ReportDisplay";
import SearchBar from "@/components/SearchBar";
import SECFilingsPanel from "@/components/SECFilingsPanel";
import StockHeader from "@/components/StockHeader";
import WatchlistSidebar from "@/components/WatchlistSidebar";
import {
  addToWatchlist,
  ApiError,
  checkApiHealth,
  fetchAnalysis,
  fetchHistory,
  fetchPastReports,
  fetchStockData,
  fetchWatchlist,
  removeFromWatchlist,
} from "@/lib/api";
import type {
  AgentStep,
  AnalysisResponse,
  HistoryEntry,
  StockData,
  WatchlistEntry,
} from "@/types";

const INITIAL_STEPS: AgentStep[] = [
  { id: "news", name: "News Agent", status: "waiting" },
  { id: "financial", name: "Financial Agent", status: "waiting" },
  { id: "sec", name: "SEC Agent", status: "waiting" },
  { id: "risk", name: "Risk Agent", status: "waiting" },
  { id: "report", name: "Report Agent", status: "waiting" },
];

const STEP_DELAYS_MS = [0, 4000, 9000, 14000, 20000];

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

  const hasResults = Boolean(stockData || analysisReport || isLoading);
  const isOnWatchlist = Boolean(
    currentTicker &&
      watchlist.some(
        (e) => e.ticker.toUpperCase() === currentTicker.toUpperCase()
      )
  );

  useEffect(() => {
    checkApiHealth().then(setApiReachable);
  }, []);

  const refreshWatchlist = useCallback(async () => {
    try {
      const wl = await fetchWatchlist();
      setWatchlist(wl);
    } catch {
      // watchlist fetch failed — keep local state
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
  }, [refreshSidebar]);

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

  function markAllComplete() {
    setAgentSteps((prev) =>
      prev.map((step) => ({ ...step, status: "complete" }))
    );
  }

  useEffect(() => {
    if (!isLoading) return;

    const timers = STEP_DELAYS_MS.map((delay, index) =>
      setTimeout(() => markStepRunning(index), delay)
    );

    return () => timers.forEach(clearTimeout);
  }, [isLoading]);

  async function runAnalysis(ticker: string) {
    const symbol = ticker.toUpperCase();
    setCurrentTicker(symbol);
    setError(null);
    setAnalysisReport(null);
    setIsLoading(true);
    resetAgentSteps();

    setRecentTickers((prev) => {
      const next = [symbol, ...prev.filter((t) => t !== symbol)];
      return next.slice(0, 8);
    });

    try {
      const stock = await fetchStockData(symbol);
      setStockData(stock);

      const analysis = await fetchAnalysis(symbol);
      markAllComplete();
      setAnalysisReport(analysis);
      setAnalyzedAt(new Date().toISOString());
      await refreshSidebar();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Analysis failed. Please try again.";
      setError(message);
      resetAgentSteps();
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
    <div className="flex min-h-screen flex-col">
      <AppHeader apiReachable={apiReachable} />

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <HeroSection compact={hasResults} />

        <section className="mt-6 sm:mt-8" aria-label="Search">
          <SearchBar
            onAnalyze={runAnalysis}
            isLoading={isLoading}
            recentTickers={recentTickers}
          />
        </section>

        {apiReachable === false && (
          <div
            className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200 sm:mt-6"
            role="alert"
          >
            Backend API is unreachable. Start FastAPI locally or set{" "}
            <code className="rounded bg-black/30 px-1 text-amber-100">
              NEXT_PUBLIC_API_URL
            </code>{" "}
            to your deployed backend.
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

        <div className="mt-6 grid grid-cols-1 gap-6 lg:mt-8 lg:grid-cols-12 lg:gap-8">
          <div className="space-y-6 lg:col-span-8 lg:space-y-8">
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

            {isLoading && <LoadingPipeline />}

            {analysisReport && !isLoading && (
              <>
                <ReportDisplay analysis={analysisReport} />
                <CitationsPanel
                  analysis={analysisReport}
                  analyzedAt={analyzedAt}
                />
              </>
            )}

            {!hasResults && !error && (
              <div className="space-y-6">
                <div className="card-surface flex flex-col items-center justify-center px-6 py-12 text-center sm:py-16">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/10 text-2xl">
                    📊
                  </div>
                  <p className="mt-5 text-lg font-semibold text-slate-200">
                    Start with any U.S. ticker
                  </p>
                  <p className="mt-2 max-w-md text-sm leading-relaxed text-slate-500">
                    Press <strong className="text-slate-400">Run analysis</strong> or pick
                    a symbol below. Your report will appear here with agent progress,
                    confidence scores, and sources.
                  </p>
                </div>
                <div>
                  <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-500">
                    How QuantPilot works
                  </h2>
                  <FeatureCards />
                </div>
              </div>
            )}
          </div>

          <aside className="space-y-6 lg:col-span-4">
            <InvestmentIdeasPanel
              onSelectTicker={runAnalysis}
              isLoadingAnalysis={isLoading}
            />
            <WatchlistSidebar
              watchlist={watchlist}
              currentTicker={currentTicker}
              onSelect={runAnalysis}
              onAdd={handleAddWatchlist}
              onRemove={handleRemoveWatchlist}
              loading={watchlistLoading}
              addDisabledReason={
                !currentTicker ? "Search a ticker first" : null
              }
            />
            <HistoryPanel
              history={history}
              onSelect={handleHistorySelect}
              loading={historyLoading}
            />
          </aside>
        </div>
      </main>

      <AppFooter />
    </div>
  );
}
