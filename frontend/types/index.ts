export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export type AgentStatus = "waiting" | "running" | "complete";

export interface AgentStep {
  id: string;
  name: string;
  status: AgentStatus;
}

export interface StockData {
  ticker: string;
  current_price: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
}

export interface Filing {
  form_type: string;
  filing_date: string;
  report_date: string | null;
  accession_number: string;
  document_url: string;
}

export interface FilingsData {
  ticker: string;
  cik: string;
  company_name: string | null;
  filings: Filing[];
}

export interface ReportSection {
  title: string;
  content: string;
}

export interface FinalReport {
  ticker: string;
  report_title: string;
  executive_summary: string;
  sections: ReportSection[];
  recommendation: string;
  disclaimer: string;
}

export interface NewsOutput {
  sentiment: "bullish" | "bearish" | "neutral";
  summary: string;
  key_themes: string[];
}

export interface MetricsOutput {
  valuation_rating: "overvalued" | "fairly valued" | "undervalued";
  analysis_summary: string;
  key_metrics: Record<string, unknown>;
}

export interface SECOutput {
  filing_summary: string;
  risk_signals: string[];
  latest_filing_type: string;
}

export interface RiskOutput {
  risk_level: RiskLevel;
  risk_factors: string[];
  confidence_score: number;
}

export interface FactItem {
  content: string;
  source: string;
}

export interface InsightItem {
  content: string;
  generated_by: string;
}

export interface FactsAndInsights {
  facts: FactItem[];
  insights: InsightItem[];
}

export interface PerAgentConfidence {
  news: number;
  financial: number;
  sec: number;
  risk: number;
  report: number;
}

export interface AnalysisResponse {
  ticker: string;
  final_report: FinalReport;
  news_output?: NewsOutput | null;
  metrics_output?: MetricsOutput | null;
  sec_output?: SECOutput | null;
  risk_output?: RiskOutput | null;
  run_id?: string;
  overall_confidence_score?: number;
  per_agent_confidence?: PerAgentConfidence;
  validation_warnings?: string[];
  facts_and_insights?: FactsAndInsights;
}

export interface WatchlistEntry {
  ticker: string;
  added_at: string;
  notes: string;
}

export interface HistoryEntry {
  ticker: string;
  timestamp: string;
  recommendation: string;
  risk_level: RiskLevel | string;
}

export interface StoredReport {
  id: string;
  metadata: Record<string, unknown>;
  report: FinalReport;
  distance?: number | null;
}

export interface MemorySearchResult {
  query: string;
  results: StoredReport[];
}

export interface RecommendationPick {
  ticker: string;
  score: number;
  outlook: "bullish" | "neutral" | "bearish";
  near_term_view: string;
  reasons: string[];
  current_price: number | null;
}

export interface RecommendationsResponse {
  disclaimer: string;
  generated_at: string;
  horizon: string;
  picks: RecommendationPick[];
  scanned_tickers: string[];
}
