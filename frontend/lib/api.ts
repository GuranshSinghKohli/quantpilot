import type {
  AnalysisResponse,
  FilingsData,
  HistoryEntry,
  MemorySearchResult,
  RecommendationsResponse,
  StockData,
  StoredReport,
  WatchlistEntry,
} from "@/types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "";

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
