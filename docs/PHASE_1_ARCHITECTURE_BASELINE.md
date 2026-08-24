# QuantPilot Phase 1 Architecture Baseline

**Date:** 2026-08-23  
**Scope:** Inspection only. Application code was not modified.  
**Source of truth:** Implementation. Where docs disagree with code, this document records the discrepancy and follows the code.

---

## 1. Executive Summary

QuantPilot is a production-style **equity investigation bench**: a Next.js 14 single-page desk plus a FastAPI backend. It already implements most of the target loop for a **single ticker**, on demand.

The product already:

- Detects price moves (absolute, idiosyncratic residual vs SPY, realized-vol z-score).
- Plans which MCP tools to call.
- Collects evidence (Yahoo Finance, SEC EDGAR, optional IR/browser).
- Ranks competing hypotheses.
- Verifies claims against evidence (“no evidence → no claim”).
- Runs a Devil’s Advocate pass.
- Persists an auditable Evidence Ledger (investigations, claims, evidence, links).
- Notifies signed-in users only when a **proactive** case clears a post-investigation materiality bar.
- Optionally sweeps holdings on a schedule (`INVESTIGATIONS_SWEEP_ENABLED`, default **off**).

It is **not yet** a continuously autonomous multi-signal investigation engine. Detection is price/vol/idio only. The investigation lifecycle is a linear Python runner, not a LangGraph graph. OpenClaw is an **opt-in fallback**, not the primary data path. Memory is report/search embeddings, not longitudinal company intelligence. Evaluation covers the research report graph, not historical-event investigation accuracy.

There are **two pipelines**. They must stay separate:

| Pipeline | Entry | Orchestration | Output |
|---|---|---|---|
| Deep research | `POST /api/analysis/{ticker}` | LangGraph `StateGraph` | Full report + investment memo |
| Investigate (main product) | `POST /api/investigations/investigate` | `investigation_runner.py` (not LangGraph) | Evidence Ledger case |

The expected architecture (Next.js → FastAPI → analysis graph **or** investigation runner → MCP → APIs/SEC/browser → Postgres/Chroma → scheduler/alerts) **is correct**, with one important qualifier: the research graph’s `fetch_data` node calls `yahoo_finance` / `sec_edgar` **directly**; MCP is used later by some research agents and by the investigation runner.

---

## 2. Current Architecture

```
                         ┌─────────────────────────────┐
                         │  Next.js 14 (single page)   │
                         │  Investigation desk + dock  │
                         └──────────────┬──────────────┘
                                        │ REST + SSE
                                        ▼
                         ┌─────────────────────────────┐
                         │  FastAPI (backend/app)      │
                         │  JWT user  or  X-Guest-Id   │
                         └───────┬───────────┬─────────┘
                                 │           │
              /api/analysis/*    │           │  /api/investigations/*
                                 ▼           ▼
              ┌─────────────────────┐   ┌──────────────────────────┐
              │ LangGraph           │   │ investigation_runner.py  │
              │ analysis_graph.py   │   │ detect → plan → collect  │
              │ 12 sequential nodes │   │ → rank → verify → DA     │
              │ (linear StateGraph) │   │ → roster → persist       │
              └──────────┬──────────┘   └────────────┬─────────────┘
                         │                           │
                         │  some agents via MCP      │  all evidence via MCP
                         ▼                           ▼
              ┌────────────────────────────────────────────────────┐
              │  MCP server (mcp_server/server.py, stdio)          │
              │  finance · SEC · news · IR/browser tools           │
              └──────────┬─────────────────────────────┬───────────┘
                         │ API                         │ optional
                         ▼                             ▼
              yfinance / SEC EDGAR              OpenClaw (opt-in)
              allowlisted httpx IR              Browser MCP fallback
                         │
                         ▼
              ┌────────────────────────────────────────────────────┐
              │  PostgreSQL or SQLite  ·  ChromaDB  ·  Redis opt.  │
              │  APScheduler: briefings, alerts, optional sweep    │
              └────────────────────────────────────────────────────┘
```

---

## 3. Frontend Architecture

The frontend is a **single App Router page**, not a multi-route dashboard.

| Area | Location | Role |
|---|---|---|
| Shell | `frontend/app/layout.tsx`, `page.tsx` | One investigation desk |
| Search | `SearchBar.tsx` | Investigate (primary) vs Deep research (secondary) |
| Case headline | `MoveHeadline.tsx` | Ticker, quote tape, move %, status, lookback |
| Causes | `case/CausesColumn.tsx` | Ranked claims + DA outcome |
| Receipts | `case/ReceiptsColumn.tsx` | Evidence items + claim links |
| Case list | `case/CaseHistory.tsx` | Past investigations |
| Case state | `case/useCase.ts` | Polls `GET /investigations/{id}` until settled |
| Chat | `InvestigationChatPanel.tsx` | Q&A against the open case |
| Tools dock | `ToolsDock.tsx` | Watchlist, holdings, briefing, alerts, ideas, reports |
| Auth | `AuthPanel.tsx` | Register / login / logout |
| API | `frontend/lib/api.ts` | Typed fetch client |
| Types | `frontend/types` | Investigation, claim, evidence, analysis |

**Investigation desk:** yes. The default UX is “frame a ticker → open a case → ranked causes + receipts.” Deep research (LangGraph report) is a secondary band that appears when a report is running or loaded.

**Portfolio / watchlist / alerts:** live in the tools dock, not as first-class routes. `PortfolioPanel`, `WatchlistSidebar`, `AlertsPanel`, `BriefingPanel`, `InvestmentIdeasPanel`, `HistoryPanel`.

**Auth flow:** JWT in `localStorage` (`getAuthToken` / `setAuthToken`). Guest investigations use `X-Guest-Id`. `fetchMe()` hydrates the header user.

**State management:** React `useState` on `page.tsx` plus `useCase`. No Redux/Zustand. Case polling stops when status is `complete`, `failed`, or `skipped_market_noise`.

**How claims/evidence display:** Causes lists ranked claims with stance and DA notes. Receipts lists evidence by `source_type` and shows which claims they support. There is **no claim–evidence graph visualization**.

**Present on disk but not mounted on the desk:** `PositionColumn.tsx` (position column was removed from `page.tsx`).

**Documentation discrepancy:** `docs/architecture/README.md` describes only ticker search + filings (original Phase 1). Ignore it. `ARCHITECTURE.md` still names `SearchBar · AgentWorkflow · ReportDisplay` as the primary UI; the shipped desk is Causes + Receipts.

---

## 4. Backend Architecture

Entry: `backend/app/main.py` — FastAPI lifespan initializes DB + APScheduler, CORS, exception handlers, optional LangSmith/Sentry/OTel.

### Routers (`/api`)

| Router | Responsibility |
|---|---|
| `auth` | Register, login, `/me` |
| `stocks` | Quote / stock snapshot |
| `filings` | SEC filings for UI |
| `analysis` | LangGraph research + SSE stream + chat |
| `investigations` | Ledger CRUD, investigate, sweep, trigger preview, search, case chat |
| `watchlist` | Track tickers (same `Holding` rows) |
| `portfolio` | Holdings, paste/snapshot sync, portfolio analysis |
| `alerts` | Rules, events, evaluate |
| `memory` | Chroma report search / past reports |
| `recommendations` | Ideas / hot picks |
| `observability` | Workflow runs, status |

### Services (selected)

| Service | Responsibility |
|---|---|
| `investigation_runner` | Full investigate lifecycle |
| `evidence_ledger_store` | Persist/load cases, claims, evidence |
| `trigger_logic` / `move_detector` | Should we investigate this move? |
| `materiality` / `investigation_notifications` | Should we notify after a case? |
| `investigation_search` | Keyword (+ optional Chroma) search |
| `investigation_roster` | Earnings / macro / brief attach |
| `watchlist_store` / `alert_store` / `briefing_store` | Domain persistence |
| `redis_store` | Cache + job queues, memory fallback |
| `yahoo_finance` / `sec_edgar` | Direct API clients (research `fetch_data` + some routes) |

### Agents

**Research graph:** news, financial, SEC, earnings, macro, risk, bull, bear, verification, report, memo.

**Investigation:** planner, hypothesis ranker, verification, Devil’s Advocate. Roster reuses earnings/macro/memo-style specialists without replacing the runner.

**Other:** portfolio agent, portfolio sync (paste / OpenClaw snapshot — no credentials).

### Workflows

Only `workflows/analysis_graph.py`. There is **no** investigation `StateGraph`.

### Auth / DB / jobs / observability / evaluation

- Auth: JWT bearer; `get_optional_user` / `get_current_user` in `auth/deps.py`.
- DB: SQLAlchemy; Postgres or SQLite (`db/session.py`).
- Scheduler: APScheduler in `jobs/scheduler.py`.
- Observability: structured logs, in-memory workflow tracker, optional exporters.
- Evaluation: `evaluation/` — research-graph confidence, validators, fact/insight split.

---

## 5. Research Graph

**File:** `backend/app/workflows/analysis_graph.py`  
**Separate from investigation:** yes. Do not merge.

### State (`AnalysisState`)

`ticker`, `run_id`, `stock_data`, `filings_data`, `news_headlines`, per-agent outputs (`news_output` … `investment_memo`), `confidence_scores`, `validation_warnings`, `facts_and_insights`, `error`.

### Actual graph (linear)

```
fetch_data
  → news_analysis
  → financial_analysis
  → sec_analysis
  → earnings_analysis
  → macro_analysis
  → risk_assessment
  → bull_debate
  → bear_debate
  → verification_analysis
  → final_report
  → investment_memo
  → END
```

No branching, no checkpoints, no human-approval node, no retries beyond per-node try/except.

### Tools / data

- `fetch_data` calls `get_stock_data`, `get_recent_filings`, `get_ticker_news` in `yahoo_finance.py` / `sec_edgar.py` **directly**.
- Later nodes may also call MCP (`news_agent`, `financial_metrics_agent`, `sec_filing_agent`, `earnings_agent` → `get_ir_materials`).
- This is a dual path (direct client + MCP), not a single MCP-only research graph.

### Execution, errors, validation, report

Each agent step: run → Pydantic validate (`evaluation/validator.py`) → heuristic confidence (`evaluation/confidence.py`) → record on `workflow_tracker`. Failures set `error_message` / confidence 0 and do not crash the graph unless `fetch_data` fails (sets `state["error"]`; later nodes no-op).

`final_report` synthesizes prior outputs and runs `separate_facts_and_insights`. `investment_memo` compresses the report into a decision brief. SSE via `stream_analysis`.

---

## 6. Investigation Pipeline

**Orchestrator:** `backend/app/services/investigation_runner.py` — sequential `async` Python, **not** LangGraph.

Verified lifecycle:

```
Trigger / request
  → Detection (detect_move  or  evaluate_trigger)
  → Planning (investigation_planner_agent)
  → Collection (MCP tools → evidence rows)
  → Hypothesis ranking
  → Verification (drop unsupported claims)
  → Devil's Advocate (stress-test leading)
  → Persistence (claims + evidence + DA audit + roster)
  → Notification (materiality, proactive reasons only)
```

### Step-by-step (code)

1. **Trigger / request**  
   - On demand: `POST /api/investigations/investigate` (`trigger_reason` default `on_demand`).  
   - Sweep: `evaluate_trigger` first; if pass, `investigate_ticker(..., trigger_reason="scheduled", move_snapshot=...)`.  
   - Optional `use_trigger_gate` on the API.

2. **Detection**  
   - Default on-demand: `detect_move` (price window). Noise skip only if `skip_if_noise`.  
   - Sweep / gate: `trigger_logic.evaluate_trigger` (idio residual, vol z, depth). Fail → `skipped_market_noise`.

3. **Planning**  
   - `plan_investigation(move)` → tools, seed hypotheses, focus questions.  
   - Allowlist: news, filings, price, history, IR, shareholder letter, fundamentals.  
   - Browser tools omitted unless `BROWSER_MCP_ENABLED`.  
   - LLM JSON with hardcoded fallback plan.

4. **Collection**  
   - Always one price evidence row.  
   - Call planned MCP tools.  
   - If API evidence is thin (**< 2 news and < 1 filing**, no IR yet) and browser MCP is on, escalate to `get_ir_materials` / `get_shareholder_letter`.  
   - Cap 24 items.

5. **Hypothesis ranking**  
   - `hypothesis_ranker_agent.rank_hypotheses`.

6. **Verification**  
   - `investigation_verification_agent.verify_hypotheses`.  
   - Claims without `evidence_indices` are dropped (FR-3). Re-enforced after DA.

7. **Devil’s Advocate**  
   - `devils_advocate_agent.stress_test_leading`.  
   - May weaken/reorder; audit stored as `da_outcome_json`.

8. **Persistence**  
   - `replace_system_evidence`, `replace_system_claims`, `set_verification_audit`, optional `set_roster_context`, `mark_complete` / `mark_failed`.

9. **Notification**  
   - `maybe_notify_investigation` after complete. Not part of detection.

**Statuses:** `planning | collecting | verifying | complete | failed | skipped_market_noise`.

**One-shot plan.** No mid-run replan, no checkpoint, no human approval.

---

## 7. MCP Architecture

**Server:** `mcp_server/server.py` (FastMCP on Python 3.10+; stdio JSON-RPC fallback).  
**Client:** `backend/app/mcp_client.py` (stdio subprocess). Timeouts: 5s default, **45s** for browser tools. Retries: 2.

| Tool | Purpose | Input | Output (shape) | Source | API vs browser | Used by investigation? |
|---|---|---|---|---|---|---|
| `get_stock_price` | Quote | `ticker` | price, prev close, change %, volume | yfinance | API | Yes (planner; also move detect) |
| `get_stock_fundamentals` | Fundamentals | `ticker` | mkt cap, P/E, EPS, yield, 52w, sector | yfinance | API | Yes (planner) |
| `get_stock_news` | Headlines | `ticker`, `limit` | `{headlines[]}` | yfinance news | API | Yes (default collect) |
| `get_price_history` | OHLCV | `ticker`, `period` | `{history[]}` | yfinance | API | Trigger vol (not stored as extra rows) |
| `get_recent_filings` | EDGAR list | `ticker`, `limit` | filings + document URLs | SEC `data.sec.gov` | API | Yes |
| `get_company_facts` | CIK / SIC / state | `ticker` | company facts | SEC submissions | API | **No** (registered only) |
| `get_ir_materials` | IR pages | `ticker`, `max_pages` | pages, excerpt, sources | httpx IR; OpenClaw if fallback | Browser/HTTP | Yes if planned or thin-API escalate |
| `fetch_browser_page` | One URL | `url` | excerpt, provider | same fetch_page | Browser/HTTP | Mapped in runner; **not** in planner allowlist |
| `get_shareholder_letter` | Letter-like IR | `ticker` | best IR page by keywords | `get_ir_materials` | Browser/HTTP | Yes if planned or escalate |
| `snapshot_active_browser_tab` | User’s open tab | none | url, text | OpenClaw existing-session | Browser only | **No** (portfolio sync) |

Research-graph usage (separate): financial/news/SEC/earnings agents call some of the same tools. `fetch_data` does **not** go through MCP.

---

## 8. OpenClaw Architecture

**Classification: C (optional) + B (fallback). Not A (primary). Not D (unused).**

Default IR path is **allowlisted httpx**. OpenClaw navigate runs only when all of these hold:

1. `BROWSER_MCP_ENABLED` is not off (default on).  
2. `OPENCLAW_IR_FALLBACK=true` (default **false**).  
3. `openclaw_enabled()` — `OPENCLAW_BROWSER_URL` set **or** `OPENCLAW_USE_CLI` on.  
4. httpx failed or returned thin content (< 120 chars).

`ARCHITECTURE.md` says browser tools “prefer OpenClaw when configured.” **Code disagrees.** Code prefers httpx; OpenClaw is opt-in fallback. Treat the code as authoritative.

### How it is invoked

`browser_tools.fetch_page` → `_fetch_http` → on failure/thin + fallback flag → `openclaw_client.fetch_page_via_openclaw`:

- HTTP: `POST {OPENCLAW_BROWSER_URL}/start`, `/navigate` (or `/tabs/open`), then `/snapshot` if text is short. Default base `http://127.0.0.1:18791`.  
- CLI: `openclaw browser … open` + `snapshot` if `OPENCLAW_USE_CLI`.

Auth headers: `OPENCLAW_GATEWAY_TOKEN` (Bearer) or `OPENCLAW_GATEWAY_PASSWORD`; profile `OPENCLAW_BROWSER_PROFILE`.

### What it can access

- IR: known IR URLs (AAPL, MSFT, …), Yahoo `website` → `/investor` / `ir.` hosts, plus always-allowed suffixes: `sec.gov`, `q4cdn.com`, `q4web.com`, `investorroom.com`, `corporate-ir.net`.  
- Host allowlist enforced on httpx and redirects.  
- `snapshot_active_browser_tab`: **no navigation**. Reads the user’s already-open tab (`OPENCLAW_BROWSER_PROFILE_EXISTING`, default `user`). Used for portfolio paste-alternative. Credentials never pass through QuantPilot.

### Timeouts / ledger

- OpenClaw HTTP ~40s; CLI ~45s; MCP browser tools 45s.  
- Browser evidence maps to `source_type` `ir_page` (or letter) and `retrieval_method` `openclaw` / `httpx` / `mcp` depending on provider.

**Product rule already encoded:** do not use OpenClaw when httpx/API works.

---

## 9. Evidence Ledger

**Verdict: A relational evidence ledger, not a true graph.**

### Tables

```
investigations 1──* claims
               1──* evidence_items
claims *──* evidence_items  via claim_evidence_links
```

| Table | Persisted |
|---|---|
| `investigations` | owner_key, user_id, ticker, trigger_reason, status, move_pct, window_label, summary, error, verification_notes, da_outcome_json, roster_json, timestamps |
| `claims` | statement, stance, confidence_score, rank, devil_advocate_notes |
| `evidence_items` | source_type, retrieval_method, title, excerpt, source_url, raw_payload_json |
| `claim_evidence_links` | relation (`supports` / `contradicts` / `context`), note |

**Provenance:** `source_url` + `retrieval_method` (`mcp | openclaw | httpx | user | system`) + raw payload.

**Verification / DA:** notes + `da_outcome_json` (outcome, counterargument, weakened/reversal, citation_coverage).

**Search:** `GET /api/investigations/search` — keyword always; Chroma `investigation_ledger` when `OPENAI_API_KEY` is set. Scoped by `owner_key`.

**Not present:** source-quality scores, as-of / temporal validity columns, contradiction edges between evidence items, cross-investigation company nodes.

**API / tests:** `routers/investigations.py`, `models/investigation_schemas.py`, `test_evidence_ledger.py`, investigation phase 8–13 tests, `test_phase11_browser.py`.

---

## 10. Database Architecture

### Models (`backend/app/db/models.py`)

| Model | Role |
|---|---|
| `User` | Email + hashed password |
| `Portfolio` | Per-user book |
| `Holding` | Watchlist **and** portfolio position (same row) |
| `Briefing` | Daily portfolio briefing |
| `AlertRule` / `AlertEvent` | Threshold alerts; events can link `investigation_id` |
| `AnalysisRun` | Per-owner research history pointer |
| `Investigation` / `Claim` / `EvidenceItem` / `ClaimEvidenceLink` | Ledger |

There is **no** SQL company-memory table. Chroma is not a SQLAlchemy model.

### Ownership / isolation

| Surface | Isolation |
|---|---|
| Investigations, analysis history, Chroma investigation search | `owner_key` = `user:{id}` or `guest:{uuid}` (`X-Guest-Id`). Queries always filter `owner_key`. |
| Watchlist / holdings / briefings / alerts | Authenticated user **or shared anonymous user** `anonymous@local.quantpilot` for guests |

**Important:** guest investigations are device-scoped (`guest:{uuid}`). Guest watchlists share one anonymous user. Do not treat those as the same isolation model.

Sweep jobs use `owner_key = f"user:{u.id}"` only (signed-in users with holdings).

**No brokerage credentials** in any model. Position import is paste or existing-session snapshot.

---

## 11. Scheduler & Autonomous Monitoring

**Autonomous monitoring: C — partially implemented; B — optional. Not default (A).**

`INVESTIGATIONS_SWEEP_ENABLED` defaults to **`false`**.

| Job | Flag (default) | Schedule | Behavior |
|---|---|---|---|
| Daily briefings | `BRIEFING_ENABLED` (**true**) | Cron UTC (`BRIEFING_HOUR_UTC` 12:00) | Portfolio Agent per user with holdings |
| Smart alerts | `ALERTS_ENABLED` (**true**) | Interval `ALERT_INTERVAL_MINUTES` (1–180) | User `AlertRule`s (price / vol / keyword news) |
| Investigation sweep | `INVESTIGATIONS_SWEEP_ENABLED` (**false**) | Interval, clamp 5–240 min (default 30) | Holdings → cooldown → trigger → investigate |

### Sweep mechanics

- Cap: `INVESTIGATION_SWEEP_MAX_PER_RUN` (default 5, max 50).  
- Cooldown: `INVESTIGATION_COOLDOWN_HOURS` (default 6) via `recent_investigation_exists`.  
- Trigger: `evaluate_trigger`; skip if not idiosyncratic / not vol-abnormal.  
- Launch: `investigate_ticker` with `trigger_reason="scheduled"`.  
- Manual: `POST /api/investigations/sweep` (auth), `GET /api/investigations/trigger/{ticker}`.  
- Jobs: `max_instances=1`, `coalesce=True` (no overlapping runs).  
- Redis: `quantpilot:queue:investigation_jobs` and alert queue; **in-memory fallback** if `REDIS_URL` unset or ping fails.  
- Idempotency: cooldown + coalesce; not a hard unique constraint on (owner, ticker, window).

---

## 12. Materiality

**Two different gates. Do not conflate them.**

| Question | Module | When | Inputs |
|---|---|---|---|
| Should we **investigate**? | `trigger_logic.py` (+ `move_detector.py`) | **Before** collect (sweep / `use_trigger_gate`) | Price move, residual vs SPY, realized-vol z, noise floor, asset class → depth |
| Should we **notify**? | `materiality.py` + `investigation_notifications.py` | **After** a complete case | Score from z, residual, move %, depth |

### Investigate trigger (pre)

Skip if: no move; residual < `INVESTIGATION_IDIO_PCT` (1.0%) and move not large; no vol and abs move < `INVESTIGATION_NOISE_PCT` (1.5%); z < `INVESTIGATION_VOL_Z_THRESHOLD` (1.5) and abs move < 2.5%.

Depth: downside more aggressive (`deep` at z≥1.0 or 3% down); upside `deep` at z≥2.0 or 5% up.

**On-demand investigate does not use this gate** unless `use_trigger_gate=true` or `skip_if_noise`.

### Notify bar (post)

Score ≈ min(40, \|z\|×12) + min(30, \|residual\|×6) + min(20, \|move\|×2.5) + depth bonus (0 / 8 / 18), capped at 100.

Notify if score ≥ 45 **and** (depth ≥ `standard` **or** \|z\| ≥ 2.0), move known, `INVESTIGATION_NOTIFY_ENABLED`, user present, `trigger_reason` ∈ `{scheduled, price_move, alert}`, status `complete`, and no `investigation_material` event for that user/ticker within `INVESTIGATION_NOTIFY_COOLDOWN_HOURS` (6).

**Portfolio exposure is not in the score.** Holdings only decide *who* gets swept / who can be notified, not *how material* the event is.

On-demand cases (`on_demand`, `manual`) never notify.

Email is a stub (`ALERT_EMAIL_ENABLED` logs only). Delivery is in-app `AlertEvent`.

---

## 13. Memory

| Kind | Exists? | Where |
|---|---|---|
| Semantic memory (reports) | Yes | Chroma `stock_analyses` (`chroma_store.py`, `report_memory.py`) |
| Semantic memory (cases) | Optional | Chroma `investigation_ledger` if `OPENAI_API_KEY` |
| Structured memory | Yes | SQL: investigations, claims, evidence, analysis_runs, briefings |
| Previous-investigation memory | Partial | SQL list + keyword/Chroma search, **owner-scoped** |
| Company-level memory | **No** | No ticker-keyed longitudinal facts |
| Temporal memory | **No** | No as-of, supersedes, guidance-vs-prior |

Session chat: in-process (`session_store.py`), not durable company intelligence.

Do not add another vector DB. Extend SQL + existing Chroma collections.

---

## 14. Evaluation

**Applies to the LangGraph research report, not the investigation engine.**

| Piece | What it measures |
|---|---|
| `confidence.py` | Heuristic 0–1 scores (news count, missing P/E, filing recency, etc.) |
| `validator.py` | Pydantic shape + safe defaults per agent output |
| `fact_separator.py` | Split report into facts vs insights with source labels |

**Does not exist:** historical-event benchmark; labels for “correct cause”; citation correctness vs live web; hallucination rate on claims; confidence calibration vs outcomes; investigation latency / tool-success dashboards; temporal leakage controls (frozen as-of news).

Investigation has `citation_coverage` on the DA payload and FR-3 filtering — operational checks, not a benchmark harness.

---

## 15. Current Strengths

1. **Investigation loop is real** — detect → plan → MCP evidence → rank → verify → DA → ledger → optional notify.  
2. **Evidence integrity rule** — unsupported claims are dropped; DA cannot invent evidence.  
3. **MCP boundary** — investigation collect goes through tools; keys stay off agent prompts.  
4. **OpenClaw discipline** — httpx first; OpenClaw opt-in; snapshot never logs into brokerages.  
5. **Owner isolation on the ledger** — `owner_key` on every investigation query.  
6. **Trigger vs notify split** already exists (even if materiality is notify-only).  
7. **Tests** for ledger, investigation phases 8–13, browser, alerts, auth.  
8. **Frontend is already an investigation desk**, not a chatbot shell.  
9. **Idempotent-ish jobs** — sweep cooldown, scheduler coalesce, Redis or memory queue.

---

## 16. Current Limitations

1. Investigation is a linear function — no graph state, branch, retry, or checkpoint.  
2. Events are price/vol/idio only — no earnings, filing, news, or macro detectors.  
3. Materiality does not decide whether to spend an investigation; it only filters alerts.  
4. Planner is one-shot; thin-evidence escalate is a hardcoded IR pair, not a replan.  
5. Ledger is relational M2M, not a provenance/contradiction/temporal graph.  
6. No company memory across cases.  
7. Sweep is off by default — product is still mostly reactive.  
8. Research `fetch_data` bypasses MCP (dual data path).  
9. Guest watchlist shares one anonymous user.  
10. `ARCHITECTURE.md` / `docs/architecture/README.md` are stale vs code.  
11. No historical investigation eval; research confidence is heuristic, not calibrated.  
12. UX has no “system opened this case” graph; Position column is unused.

---

## 17. PRD Gap Analysis

| Target capability | Current status | Gap | Relevant files |
|---|---|---|---|
| Event detection | Price / residual / vol z only | Add filings, earnings window, news, macro as first-class events | `move_detector.py`, `trigger_logic.py`, `investigation_sweep_job.py` |
| Materiality | Post-case notify score; no exposure | Pre-investigate gate; portfolio weight / fundamentals | `materiality.py`, `investigation_notifications.py` |
| Dynamic planning | One-shot LLM plan + thin-IR escalate | Replan on thin/contradictory evidence | `investigation_planner_agent.py`, `investigation_runner.py` |
| LangGraph investigation orchestration | **Absent** (research graph only) | Wrap existing runner steps as nodes; do not rewrite | `investigation_runner.py`, `workflows/analysis_graph.py` (pattern only) |
| Hypothesis engine | Ranker + FR-3 | Priors from company memory; standing hypotheses | `hypothesis_ranker_agent.py` |
| MCP | 10 tools; investigation uses 7 | Do not add tools until a detector/planner needs them; `get_company_facts` unused | `mcp_server/server.py`, `mcp_client.py` |
| OpenClaw | Optional fallback (off by default) | First-class *planned* primary-source step when APIs are thin; keep httpx default | `openclaw_client.py`, `browser_tools.py` |
| Evidence graph | Relational ledger + links | Quality, as-of, contradiction edges; keep SQL | `models.py`, `evidence_ledger_store.py` |
| Verification | Investigation verifier + citation coverage | Calibration, cross-claim contradiction | `investigation_verification_agent.py` |
| Devil’s Advocate | Implemented + persisted | Use graph contradiction fields; don’t replace | `devils_advocate_agent.py` |
| Company memory | Chroma search only | Append-only ticker facts with as-of / supersedes | `investigation_chroma.py`, new SQL later |
| Autonomous monitoring | Sweep implemented, default off | Safe default-on with caps; multi-signal | `scheduler.py`, `investigation_sweep_job.py` |
| Evaluation | Research-graph heuristics | Historical events, leakage rules, investigation metrics | `backend/app/evaluation/` |
| Investigation UX | Strong on-demand desk | System-opened cases; claim–evidence graph view | `page.tsx`, `CausesColumn.tsx`, `ReceiptsColumn.tsx` |

---

## 18. Recommended Phase Order

Build on existing files. Smallest coherent change per phase. **Do not start Phase 2 until this baseline is accepted.**

| Phase | Build on | Do |
|---|---|---|
| **2** Event detection + materiality | `trigger_logic`, `move_detector`, `materiality`, sweep | 1–2 new event types; optional **pre-investigate** materiality; keep price/idio |
| **3** Dynamic planning | planner + runner | Wrap runner steps in LangGraph; one replan-if-thin branch; **same functions as nodes** |
| **4** Hypotheses | ranker | Ranking improvements; memory priors only after Phase 8 or a thin stub |
| **5** Evidence graph | four ledger tables | Add columns/edges; graph read API; no new database |
| **6** Browser / OpenClaw | `browser_tools`, planner `BROWSER_TOOLS` | Planner may request primary source when APIs thin; persist `openclaw`; httpx remains default |
| **7** Verify + DA | existing agents | Use new graph fields; keep FR-3 |
| **8** Company memory | new ticker-keyed SQL + Chroma | Append-only facts; do not replace report collection |
| **9** Autonomous monitoring | sweep + notify | Default-on with caps/cooldown; still notify-gated |
| **10** UX | case desk | System-opened case + link viz; keep cyberpunk desk |
| **11** Historical eval | new eval package | Frozen events, no-hindsight rules; can start after Phase 5 schema |
| **12** Harden | CI, observability | Trace investigation graph; no public API churn |

**Sequencing rules**

- Phase 3 wraps `investigation_runner`; it does not delete it.  
- CrewAI / OpenAI Agents SDK: **not in Phase 2–4**. Only after LangGraph investigation exists, and only as a subcomponent.  
- Do not create a third pipeline.  
- Phase 11 can overlap UX once the ledger schema is stable.

---

## 19. Things NOT to Rebuild

Future phases must preserve:

1. `investigation_runner` lifecycle and REST contracts on `/api/investigations/*`.  
2. Evidence Ledger tables and `owner_key` isolation.  
3. FR-3: no evidence → no claim.  
4. MCP as the investigation I/O boundary.  
5. httpx-first IR; OpenClaw not default.  
6. No brokerage credentials / no QuantPilot login to brokers.  
7. LangGraph **research** graph (`analysis_graph.py`) as the deep-report path — do not replace it with the investigation runner or vice versa.  
8. PostgreSQL/SQLite + existing Chroma collections.  
9. Sweep cooldown, notify cooldown, scheduler coalesce.  
10. Frontend investigation desk (Causes / Receipts / `useCase`) — extend, don’t replace with a chatbot.  
11. Pydantic structured agent outputs and existing investigation tests as regression anchors.  
12. Guest `X-Guest-Id` for ledger/history (do not silently merge with anonymous watchlist).

---

## 20. Engineering Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Duplicate / third pipeline | Two pipelines already; a “new autonomous engine” rewrite would fork the product | Extend runner + wrap in LangGraph; never add a third entry point |
| Unnecessary frameworks | CrewAI / Agents SDK / extra vector DBs add cost without closing the top gaps | Phase 2–5 stay on current deps |
| OpenClaw overuse | Slow, flaky, easy to treat as a scraper | Keep `OPENCLAW_IR_FALLBACK` default off; APIs first |
| Breaking REST contracts | Frontend `api.ts` + tests depend on current shapes | Additive fields only unless a later phase requires a versioned change |
| Evidence integrity | LLMs will invent citations | Keep FR-3, provenance columns, no fabricated rows |
| Hallucination | Ranker/DA can still overfit headlines | Verification + DA + later calibration/eval |
| Temporal leakage | Future eval will be worthless if models see post-event news | Phase 11 must freeze as-of corpora; don’t start “eval” by scoring live cases |
| Runaway autonomous jobs | Sweep × many users × deep IR/OpenClaw | Keep caps, cooldown, coalesce; don’t default-on until Phase 9 hardening |
| Dual fetch paths | Research `fetch_data` bypasses MCP | Don’t “fix” in Phase 2 unless needed; don’t spread more direct clients |
| Guest isolation mismatch | Shared anonymous watchlist vs per-device ledger | Don’t “unify” without an explicit product decision |
| Stale docs | `ARCHITECTURE.md` / `docs/architecture/README.md` mislead | This file is the Phase 1 baseline; do not silently rewrite those docs here |

---

## Documentation discrepancies (recorded, not fixed)

| Document | Claim | Code |
|---|---|---|
| `docs/architecture/README.md` | Only stocks/filings/health; “AI out of scope” | Full investigation + LangGraph stack |
| `ARCHITECTURE.md` diagram | “5 AI agents”, “6 financial tools” | 11 research nodes + investigation agents; 10 MCP tools |
| `ARCHITECTURE.md` MCP blurb | Browser tools “prefer OpenClaw when configured” | httpx first; OpenClaw only if `OPENCLAW_IR_FALLBACK` |
| `ARCHITECTURE.md` body (investigation) | Largely accurate | Follow the body over the diagram |

---

## Phase 1 confirmation

- Application source: **unchanged**.  
- Dependencies: **not installed**.  
- Phase 2: **not started**.  
- Only artifact of this phase: this file.
