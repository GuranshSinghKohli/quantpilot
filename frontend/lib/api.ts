import type {
  AnalysisResponse,
  ChatResponse,
  FilingsData,
  HistoryEntry,
  MemorySearchResult,
  PortfolioAnalysis,
  RecommendationsResponse,
  StockData,
  StoredReport,
  WatchlistEntry,
} from "@/types";

function normalizeApiUrl(raw: string): string {
  const trimmed = raw.replace(/\/$/, "");
  if (!trimmed) return "";
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed;
  }
  return `https://${trimmed}`;
}

export const API_URL = normalizeApiUrl(
  process.env.NEXT_PUBLIC_API_URL ?? ""
);

const DEFAULT_TIMEOUT_MS = 30_000;
const ANALYSIS_TIMEOUT_MS = 180_000;

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function getBaseUrl(): string {
  if (API_URL) return API_URL;
  if (process.env.NODE_ENV === "development") {
    return "http://localhost:8000";
  }
  throw new ApiError(
    "API URL is not configured. Set NEXT_PUBLIC_API_URL in Vercel or .env.local (see .env.local.example)."
  );
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string" && body.detail.length > 0) {
      return body.detail;
    }
    if (typeof body?.error === "string") return body.error;
    if (body?.detail) return JSON.stringify(body.detail);
  } catch {
    // ignore
  }
  return `Request failed (${response.status})`;
}

async function apiFetch<T>(
  path: string,
  options?: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options ?? {};
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const headers: Record<string, string> = {
      ...(fetchOptions.headers as Record<string, string>),
    };
    if (fetchOptions.body !== undefined && fetchOptions.body !== null) {
      headers["Content-Type"] = headers["Content-Type"] ?? "application/json";
    }

    const response = await fetch(`${getBaseUrl()}${path}`, {
      ...fetchOptions,
      signal: controller.signal,
      headers,
    });

    if (!response.ok) {
      throw new ApiError(await parseError(response), response.status);
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new ApiError(
        "Request timed out. The analysis pipeline can take up to 2 minutes — please try again."
      );
    }
    const base = getBaseUrl();
    throw new ApiError(
      `Unable to reach the API at ${base}. Check that the backend is running and NEXT_PUBLIC_API_URL is correct.`
    );
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function checkApiHealth(): Promise<boolean> {
  try {
    await apiFetch<{ status: string }>("/health", { timeoutMs: 8_000 });
    return true;
  } catch {
    return false;
  }
}

export async function fetchStockData(ticker: string): Promise<StockData> {
  return apiFetch<StockData>(`/api/stocks/${encodeURIComponent(ticker)}`);
}

export async function fetchFilings(ticker: string): Promise<FilingsData> {
  return apiFetch<FilingsData>(`/api/filings/${encodeURIComponent(ticker)}`);
}

export async function fetchAnalysis(ticker: string): Promise<AnalysisResponse> {
  return apiFetch<AnalysisResponse>(
    `/api/analysis/${encodeURIComponent(ticker)}`,
    { method: "POST", timeoutMs: ANALYSIS_TIMEOUT_MS }
  );
}

export type StreamAnalysisCallbacks = {
  onAgentStarted?: (agent: string) => void;
  onAgentCompleted?: (agent: string, confidence?: number) => void;
  onError?: (message: string) => void;
};

export async function fetchAnalysisStream(
  ticker: string,
  callbacks: StreamAnalysisCallbacks = {}
): Promise<AnalysisResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ANALYSIS_TIMEOUT_MS);

  type StreamEvent = {
    type: string;
    agent?: string;
    confidence?: number;
    message?: string;
    data?: AnalysisResponse;
  };

  function handleEvent(event: StreamEvent): AnalysisResponse | null {
    if (event.type === "agent_started" && event.agent) {
      callbacks.onAgentStarted?.(event.agent);
    } else if (event.type === "agent_completed" && event.agent) {
      callbacks.onAgentCompleted?.(event.agent, event.confidence);
    } else if (event.type === "error") {
      callbacks.onError?.(event.message ?? "Analysis failed.");
      throw new ApiError(event.message ?? "Analysis failed.");
    } else if (event.type === "result" && event.data) {
      return event.data;
    }
    return null;
  }

  function parseLine(line: string): AnalysisResponse | null {
    if (!line.startsWith("data: ")) return null;
    const raw = line.slice(6).trim();
    if (!raw) return null;
    try {
      return handleEvent(JSON.parse(raw) as StreamEvent);
    } catch {
      return null;
    }
  }

  try {
    const response = await fetch(
      `${getBaseUrl()}/api/analysis/${encodeURIComponent(ticker)}/stream`,
      { method: "POST", signal: controller.signal }
    );

    if (!response.ok) {
      throw new ApiError(await parseError(response), response.status);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new ApiError("Streaming not supported by this browser.");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const result = parseLine(line);
        if (result) return result;
      }
    }

    // Flush any remaining bytes (last event may lack trailing newline)
    buffer += decoder.decode();
    for (const line of buffer.split("\n")) {
      const result = parseLine(line);
      if (result) return result;
    }

    throw new ApiError("Analysis stream ended without a result.");
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new ApiError(
        "Request timed out. The analysis pipeline can take up to 2 minutes — please try again."
      );
    }
    throw new ApiError(
      `Unable to reach the API at ${getBaseUrl()}. Check that the backend is running.`
    );
  } finally {
    clearTimeout(timeoutId);
  }
}

/** Stream first; fall back to standard POST (more reliable on free-tier hosts). */
export async function fetchAnalysisWithFallback(
  ticker: string,
  callbacks: StreamAnalysisCallbacks = {}
): Promise<AnalysisResponse> {
  try {
    return await fetchAnalysisStream(ticker, callbacks);
  } catch (streamError) {
    if (streamError instanceof ApiError) {
      callbacks.onError?.(streamError.message);
    }
    return fetchAnalysis(ticker);
  }
}

export async function sendChatMessage(
  ticker: string,
  question: string,
  analysisContext?: Record<string, unknown>
): Promise<ChatResponse> {
  return apiFetch<ChatResponse>(`/api/chat/${encodeURIComponent(ticker)}`, {
    method: "POST",
    body: JSON.stringify({
      question,
      analysis_context: analysisContext ?? null,
    }),
    timeoutMs: 60_000,
  });
}

export async function fetchPortfolioAnalysis(): Promise<PortfolioAnalysis> {
  return apiFetch<PortfolioAnalysis>("/api/portfolio/analyze", {
    timeoutMs: 60_000,
  });
}

export async function fetchWatchlist(): Promise<WatchlistEntry[]> {
  return apiFetch<WatchlistEntry[]>("/api/watchlist");
}

export async function addToWatchlist(ticker: string): Promise<WatchlistEntry> {
  const symbol = ticker.trim().toUpperCase();
  return apiFetch<WatchlistEntry>(
    `/api/watchlist/${encodeURIComponent(symbol)}`,
    { method: "POST" }
  );
}

export async function removeFromWatchlist(ticker: string): Promise<void> {
  await apiFetch<{ status: string }>(
    `/api/watchlist/${encodeURIComponent(ticker)}`,
    { method: "DELETE" }
  );
}

export async function fetchHistory(): Promise<HistoryEntry[]> {
  return apiFetch<HistoryEntry[]>("/api/memory/history");
}

export async function fetchPastReports(ticker: string): Promise<StoredReport[]> {
  return apiFetch<StoredReport[]>(
    `/api/memory/reports/${encodeURIComponent(ticker)}`
  );
}

export async function searchMemory(query: string): Promise<MemorySearchResult> {
  const params = new URLSearchParams({ q: query });
  return apiFetch<MemorySearchResult>(`/api/memory/search?${params}`);
}

export async function fetchRecommendations(
  tickers?: string[]
): Promise<RecommendationsResponse> {
  const params = new URLSearchParams({ limit: "5" });
  if (tickers?.length) {
    params.set("tickers", tickers.join(","));
  }
  return apiFetch<RecommendationsResponse>(
    `/api/recommendations?${params}`,
    { timeoutMs: 60_000 }
  );
}
