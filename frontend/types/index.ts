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

export interface InvestmentMemo {
  ticker: string;
  memo_title: string;
  one_liner: string;
  investment_thesis: string;
  key_numbers: string[];
  catalysts: string[];
  risks: string[];
  bull_case_summary: string;
  bear_case_summary: string;
  decision: "BUY" | "HOLD" | "SELL" | "WATCH";
  conviction: "low" | "medium" | "high";
  time_horizon: string;
  what_would_change_my_mind: string[];
  disclaimer: string;
  confidence_score?: number;
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

export interface EarningsOutput {
  earnings_summary: string;
  tone: "positive" | "mixed" | "negative" | "unknown";
  key_points: string[];
  next_catalyst: string;
  sources?: string[];
  confidence_score?: number;
}

export interface IrPage {
  url: string;
  title?: string;
  provider?: string;
  text_excerpt?: string;
  char_count?: number;
}

export interface IrMaterialsOutput {
  ticker?: string;
  enabled?: boolean;
  provider?: string;
  sources?: string[];
  pages?: IrPage[];
  excerpt?: string;
  error?: string;
}

export interface MacroOutput {
  macro_summary: string;
  relevance: "high" | "medium" | "low" | "none";
  themes: string[];
  portfolio_implications: string[];
  confidence_score?: number;
}

export interface VerificationOutput {
  verified_claims: string[];
  unsupported_claims: string[];
  coverage_notes: string[];
  groundedness_score: number;
  confidence_score?: number;
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

export interface DebateSide {
  stance: "bull" | "bear";
  thesis: string;
  key_points: string[];
  confidence_score: number;
}

export interface DebateOutput {
  bull: DebateSide;
  bear: DebateSide;
}

export interface PerAgentConfidence {
  news: number;
  financial: number;
  sec: number;
  earnings?: number;
  macro?: number;
  risk: number;
  bull: number;
  bear: number;
  verification?: number;
  report: number;
  memo?: number;
}

export interface AnalysisResponse {
  ticker: string;
  final_report: FinalReport;
  investment_memo?: InvestmentMemo | null;
  news_output?: NewsOutput | null;
  metrics_output?: MetricsOutput | null;
  sec_output?: SECOutput | null;
  earnings_output?: EarningsOutput | null;
  ir_materials?: IrMaterialsOutput | null;
  macro_output?: MacroOutput | null;
  risk_output?: RiskOutput | null;
  verification_output?: VerificationOutput | null;
  debate_output?: DebateOutput | null;
  run_id?: string;
  overall_confidence_score?: number;
  per_agent_confidence?: PerAgentConfidence;
  validation_warnings?: string[];
  facts_and_insights?: FactsAndInsights;
}

export interface ChatResponse {
  ticker: string;
  answer: string;
  sources_used: string[];
}

export interface PortfolioHolding {
  ticker: string;
  current_price: number | null;
  pe_ratio: number | null;
  risk_level: string;
  valuation: string;
  weight_pct: number;
  shares?: number | null;
  avg_cost?: number | null;
  market_value?: number | null;
  unrealized_gain_pct?: number | null;
}

export interface PortfolioAnalysis {
  holdings: PortfolioHolding[];
  avg_pe: number | null;
  risk_mix: Record<string, number>;
  sector_note: string;
  weakest_ticker: string | null;
  summary: string;
  disclaimer: string;
  total_market_value?: number | null;
  weighted_by_real_positions?: boolean;
}

export interface SyncedPosition {
  ticker: string;
  shares?: number | null;
  avg_cost?: number | null;
  market_value?: number | null;
  raw_line?: string;
}

export interface PortfolioSyncOutput {
  positions: SyncedPosition[];
  broker_guess: string;
  warnings: string[];
  confidence_score: number;
  source: string;
}

export interface PortfolioSummary {
  id: number;
  name: string;
  holdings_count: number;
  holdings: WatchlistEntry[];
}

export interface DailyBriefing {
  id: number;
  generated_at: string;
  headline: string;
  summary: string;
  highlights: string[];
  risks: string[];
  watch_tickers: string[];
  holdings_snapshot: PortfolioHolding[];
  confidence_score: number;
  status: string;
  error_message: string;
  disclaimer: string;
}

export type AlertType =
  | "price_above"
  | "price_below"
  | "volatility_pct"
  | "news_sentiment";

export interface AlertRule {
  id: number;
  ticker: string;
  alert_type: AlertType | string;
  threshold: number;
  enabled: boolean;
  cooldown_minutes: number;
  last_triggered_at: string | null;
  created_at: string;
  note: string;
}

export interface AlertEvent {
  id: number;
  rule_id: number | null;
  ticker: string;
  alert_type: string;
  title: string;
  message: string;
  observed_value: number | null;
  threshold: number | null;
  investigation_id?: number | null;
  materiality_score?: number | null;
  created_at: string;
  read_at: string | null;
  is_read: boolean;
}

export interface WatchlistEntry {
  ticker: string;
  added_at: string;
  notes: string;
  shares?: number | null;
  avg_cost?: number | null;
  source?: string;
}

export interface AuthUser {
  id: number;
  email: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
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

/** PRD v3 Phase 7 — Evidence Ledger */
export type InvestigationStatus =
  | "planning"
  | "collecting"
  | "verifying"
  | "complete"
  | "failed"
  | "skipped_market_noise";

export interface EvidenceItem {
  id: number;
  source_type: string;
  retrieval_method: string;
  title: string;
  excerpt: string;
  source_url: string;
  created_at: string;
}

export interface ClaimEvidenceLink {
  id: number;
  evidence_id: number;
  relation: string;
  note: string;
  evidence?: EvidenceItem | null;
}

export interface Claim {
  id: number;
  statement: string;
  stance: string;
  confidence_score: number;
  rank: number;
  devil_advocate_notes: string;
  evidence_links: ClaimEvidenceLink[];
  created_at: string;
}

export interface InvestigationSummary {
  id: number;
  ticker: string;
  trigger_reason: string;
  status: InvestigationStatus | string;
  move_pct?: number | null;
  window_label: string;
  summary: string;
  created_at: string;
  completed_at?: string | null;
  claims_count: number;
  evidence_count: number;
}

export interface DevilsAdvocateOutcome {
  outcome: string;
  counterargument: string;
  leading_weakened: boolean;
  reversal: boolean;
  notes?: string[];
  confidence_delta?: number;
  citation_coverage?: number;
}

export interface InvestigationRosterContext {
  earnings?: Record<string, unknown>;
  macro?: Record<string, unknown>;
  memo?: Record<string, unknown>;
}

export interface InvestigationDetail extends InvestigationSummary {
  error_message: string;
  verification_notes?: string;
  da_outcome?: DevilsAdvocateOutcome | null;
  roster?: InvestigationRosterContext | null;
  claims: Claim[];
  evidence_items: EvidenceItem[];
}

export interface InvestigationSweepResponse {
  evaluated: number;
  launched: number;
  skipped_cooldown: number;
  skipped_trigger: number;
  errors: number;
  dry_run: boolean;
  details: Array<Record<string, unknown>>;
}

export interface TriggerPreviewResponse {
  ticker: string;
  should_investigate: boolean;
  reason: string;
  depth: string;
  asset_class: string;
  move_pct?: number | null;
  realized_vol_pct?: number | null;
  move_zscore?: number | null;
  benchmark_move_pct?: number | null;
  residual_pct?: number | null;
  window_label: string;
}
