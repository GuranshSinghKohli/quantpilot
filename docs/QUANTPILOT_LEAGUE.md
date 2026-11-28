# QuantPilot League — Design Doc

An AI agent arena on the research desk. A fixed roster of agents, each with $1,000 of paper money, trades the same US universe on the same days. A grounded agent must cite real news and a path through QuantPilot's knowledge graph. An ungrounded agent is the plain-LLM baseline: news only, no graph. The Evaluation Lab runs every config over the same historical bars and reports the difference.

Status: v1 design, arena pivot November 2026 · Owner: Guransh Singh Kohli · Last updated: 28 November 2026

This project is built to learn and for a portfolio. It does not have real users. There is no signup, no human league, and no player-facing game.

This file is the League spec and the single source of truth. In League instructions, "DESIGN.md" means this file. Root `DESIGN.md` is the QuantPilot UI design system. It stays untouched. The League frontend reuses that file's colors, typography, spacing, and components. The 3D and effects libraries below are additions on top of it, not replacements.

Arena pivot, November 2026. Section 6.2 is applied in undeployed migration 0001. The test database is rebuilt from that file. The dev database `quantpilot` is not migrated by this step, so its 10140 bars stay.

- Removed from the product: human leagues, invite codes, league membership, player-facing seasons, achievements, XP, levels, the trophy room, approve mode, weekly recap cards and OG images, player onboarding, and a player-order UI.
- Kept in the simulator, already built and tested, and not exposed in the UI: `validate_player_order` and orders with `source = 'player'`.
- Added: the Agent Arena (section 1), the Evaluation Lab (section 11), per-cycle metrics (section 17), and the Week 4 screens (section 3).

Amendments recorded before Week 1 code, against the November 2026 PDF:

- All section 6 tables live in Postgres schema `league`. League migrations use their own Alembic version table, `league.alembic_version`, and never modify the `public` schema.
- There is no `league.users` and no Supabase Auth. `league.players` points at the existing `public.users` integer id. `league.portfolios` is the cash book, not the `public.portfolios` watchlist.
- League models use their own `MetaData(schema="league")` and the existing sync engine and session factory (`psycopg2`). No `asyncpg`. Database access goes through `backend/app/league/repo/`. Fill and risk are pure functions.
- Market data is Massive (formerly Polygon), the client QuantPilot already uses. Week 1 is the free Stocks Basic plan: historical 1-minute aggregates, 5 calls/minute. `DATA_DELAY_MINUTES` stays 0. Live play is later and would use Massive Starter with `DATA_DELAY_MINUTES=15`. There is no Alpaca client and no IEX latency check.
- One-minute bars are stored in `league.bars`. Redis keeps only the latest price. Fills use the first existing bar strictly after the order time.
- `league.rationales.system` defaults to false. Only the simulator may set it true. An empty graph path and empty news ids are allowed then. An ungrounded rationale (`grounding=false`) may also omit the graph path. It still needs real news ids. The UI labels system trades "Auto stop-loss".
- Tests and the one-day replay read `backend/tests/fixtures/universe_small.csv`. `scripts/build_universe.py` writes `config/universe.csv`. The loader path is `UNIVERSE_PATH`.

## 0. How to use this document

This is the single source of truth for building QuantPilot League. Follow it exactly. Ask instead of guessing when something is not specified here.

1. Do not invent APIs, endpoints, or library functions. If a third-party API behaves differently from what is written here, the official docs win — update this file.
2. Every number in this file is a default, stored in config (section 15), never hard-coded.
3. Paper money only. Nothing in this codebase may place real orders with a broker.
4. Section 19 lists everything that is explicitly out of scope for v1. Do not build it.

## 1. Product summary

### 1.1 What it is

QuantPilot League extends the existing research desk (Kafka + PySpark news pipelines, Neo4j company knowledge graph, LangGraph research agents, FastAPI, PostgreSQL) into an agent arena:

- A fixed roster of agent configs. Each config is one agent with its own $1,000 paper portfolio.
- Every agent trades the same universe on the same days. The scheduler runs them on every XNYS session.
- They are ranked live against each other and against SPY.
- A grounded trade shows the news ids and the graph path (for example TSMC —SUPPLIES→ NVIDIA —COMPETES_WITH→ AMD) as a 3D graph. An ungrounded trade shows the news ids and is marked `grounding=false`.
- The Evaluation Lab (section 11) runs the same configs over one historical window and stores the comparison. The main experiment is grounded versus ungrounded, same model and same style.

The roster is not the full cartesian product of every style, risk level, and model. That would spend the token budget on combinations the experiment does not need. The roster is a checked-in file, `config/arena_roster.csv`, with columns `name,style,risk_level,llm_model,grounding_enabled,interests`. Each grounded row has an ungrounded twin: the same style, risk level, model, and interests, with `grounding_enabled=false`. The first rows are chosen at the start of Week 2. They have to fit `AGENT_DAILY_TOKEN_BUDGET` and `MARKET_REQUESTS_PER_MINUTE`. The model string is the provider's model id. This spec does not pick one.

`style` is `news_driven`, `momentum`, `value_tilt`, or `contrarian` (section 8.5). `risk_level` is `conservative`, `balanced`, or `aggressive` (section 9). `grounding_enabled=true` means the cycle loads the Neo4j subgraph and a rationale is stored only when the graph path validates. `grounding_enabled=false` means news only. The graph-path requirement is off. The rationale is stored with `grounding=false`.

Both kinds still go through `apply_risk`. Both must cite news ids that exist, are not blank, and are not published after the game clock. A stop-loss is still `system=true` and is not an LLM rationale.

All of these agents belong to one system player and one arena league and season, so the foreign keys already in section 6 still resolve. That ownership is bookkeeping. It is not a human membership. Section 6.2 says what has to change so more than one agent can sit on that one player.

### 1.2 What makes it different from "an LLM picks stocks"

| Typical AI trading project | QuantPilot League |
| --- | --- |
| LLM says "buy X" with a vague reason | A grounded trade cites real news ids and a graph path checked against Neo4j. An ungrounded trade still cites real news ids, and the row is flagged |
| Fills at whatever price is convenient | Simulator: market hours, next-bar fills, slippage, no look-ahead |
| LLM controls position size | Deterministic risk engine (plain Python) has the final say |
| One demo prompt | A fixed roster, ranked against each other and against SPY, plus a paired grounded/ungrounded run |
| "It made money" claims | The lab reports return, Sharpe, and drawdown, including the losing windows, and it states the LLM-knowledge caveat (section 11) |

### 1.3 Who it is for

The author, as a learning project and a portfolio piece. There is no user account, no invite, and no launch to a club.

## 2. Core concepts and glossary

| Term | Definition |
| --- | --- |
| Arena | The one system league. Every roster agent trades inside it. |
| Roster | The checked-in list of agent configs. Not a user-built agent. |
| Agent | One config: style, risk level, LLM model, `grounding_enabled`, and interests. One paper portfolio. |
| Portfolio | Cash + positions for one agent. Starts at $1,000.00. The arena uses `kind = 'actual'` only. |
| Ghost portfolio | Still in the schema and the tests. The arena does not create one. It existed for a human-versus-agent view that this product does not have. |
| Player order | Still accepted by `validate_player_order` and by `source = 'player'`. Not exposed in the UI and not sent by the arena scheduler. |
| Decision cycle | One run of the agent: gather context → analyze → propose orders → validate → risk check → execute → explain. |
| Order | A request to buy or sell. Market orders only. |
| Fill | The simulated execution at one price and time. |
| Rationale | The explanation on an agent order: summary, news ids, graph path, confidence, and `grounding`. |
| Grounded | `grounding_enabled=true`. The path is required and checked against Neo4j. |
| Ungrounded | `grounding_enabled=false`. News only. The path may be empty. `rationales.grounding` is false. |
| Universe | Tradable tickers. Default: S&P 500 constituents plus the fixed ETF list, US only. |
| Game clock | The time the simulator treats as now. Real time minus `DATA_DELAY_MINUTES` (section 7.2). |
| Evaluation run | One lab job: every roster config, one historical window, one seed, stored results. |

## 3. User experience (screens and flows)

Week 4. No screen here is built. There is no onboarding and no form that creates an agent.

### 3.1 Screens

| Route | Screen | Purpose |
| --- | --- | --- |
| `/` | Landing | 3D market galaxy. Describes the arena. No sign-up |
| `/arena` | Leaderboard | Live rank of the roster against each other and against SPY |
| `/agents/[id]` | Agent | Equity curve vs SPY, positions, trade feed |
| `/trades/[id]` | Trade detail | Why this agent traded: summary, news, and the 3D graph when the rationale is grounded |
| `/evaluation` | Evaluation Lab | Stored runs. The grounded-versus-ungrounded table is the first view |
| `/evaluation/[id]` | One run | Per-config return vs SPY, Sharpe, max drawdown, trade count, validation rejects, unsupported-claim rate, cost, latency |

### 3.2 What is not on screen

Human leagues, invite codes, membership, player-facing seasons, achievements, XP, levels, a trophy room, approve mode, weekly recap cards, OG images, player onboarding, and a player-order ticket. `validate_player_order` stays in the simulator and is not called from the UI.

### 3.3 Config changes

The roster file is the config. Editing it is a code change, not a click in the app. A live agent does not change style, risk, model, or grounding in the middle of a session. The lab reads the same file, so a run names the roster it used.

### 3.4 Modes

- Grounded: the cycle loads candidate paths from Neo4j. Validation requires a non-empty path (section 8.4).
- Ungrounded: the cycle does not load paths. Validation does not require a path. News rules still apply. The stored rationale has `grounding=false`.
- Execution is automatic after the risk check. There is no approve step. `pending_approval` stays in the status check so the existing tests still mean something. `create_order` does not write that status. The UI does not send it.

## 4. Tech stack

### 4.1 Existing QuantPilot components (keep)

| Component | Technology | Role in League |
| --- | --- | --- |
| Streaming | Kafka (local/dev: Redpanda, Kafka-API compatible) | Event backbone: news, prices, decisions, fills |
| Stream processing | PySpark Structured Streaming | News enrichment: entity linking (ticker detection), dedup, impact scoring |
| Knowledge graph | Neo4j (Python driver `neo4j`) | Companies, sectors, themes, supply/compete/invest relationships |
| Agents | LangGraph (`langgraph`) + `langchain-anthropic` or `langchain-openai` | Decision cycle graph |
| API | FastAPI + Uvicorn, Pydantic v2 | REST + WebSocket API |
| Database | PostgreSQL with SQLAlchemy 2.x (sync, psycopg2) + a separate Alembic env | Game state in schema `league` |

League models use `MetaData(schema="league")` and the app's single engine. `app.core.db` builds that engine from `DATABASE_URL` and exposes `SessionLocal`. `app.db.session` re-exports `SessionLocal`. League repository functions take the caller's `Session` from that factory and do not open a second engine. `asyncpg` is not a dependency. Local development sets `DATABASE_URL` to `postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot`.

### 4.2 New backend components

| Component | Technology | Why |
| --- | --- | --- |
| Market data | Massive (formerly Polygon) via the existing `SourceClient` | Week 1: Stocks Basic, historical 1-minute aggregates, 5 calls/minute. The League wrapper does not change `backend/app/market/`. See 7.2. |
| Market calendar | `exchange_calendars`, exchange `XNYS`, offline | Session open/close, holidays, early closes. No network call. |
| News | Existing Massive news adapter (`NEWS_API_KEY`) | League does not add a second news vendor. |
| Cache / leaderboard / pub-sub | Redis (`redis` Python package, asyncio API) | Latest prices, the arena sorted set, pub/sub to the WebSocket hub, the lab's response cache |
| Scheduler | APScheduler (`apscheduler`, `AsyncIOScheduler`) | Decision cycles on each XNYS session, leaderboard snapshots, cycle metrics |
| Kafka client | `confluent-kafka` (or `aiokafka`) | Produce/consume from Python services |
| Auth | Existing QuantPilot auth (section 6.1) | The desk already has it. The arena UI is read-only and does not add accounts. Not wired in Week 1 or Week 2. |
| Analytics/maths | pandas, numpy | Metrics (returns, Sharpe, drawdown), backtests |
| HTTP client | httpx | External calls |
| Logging | structlog | Structured logs with `cycle_id`, `agent_id` |
| Testing / lint | pytest, pytest-asyncio, hypothesis, ruff, mypy | Quality |

### 4.3 Frontend core

The League UI follows root `DESIGN.md`: its colors, typography, spacing, and existing components. Packages in this table and in section 4.4 are additions. They do not replace that design system.

| Package | Purpose |
| --- | --- |
| `next` (16.x, App Router) + `react` / `react-dom` (19.x) + typescript | Framework |
| `tailwindcss` (v4) | Styling |
| shadcn/ui (installed via `npx shadcn@latest init`, components copied into the repo) | Accessible base components (Button, Dialog, Tabs, Table, Tooltip, Sheet) |
| `lucide-react` | Icons |
| `@tanstack/react-query` | Server state, caching, refetching |
| `zustand` | Client state (live prices, WebSocket status, UI) |
| Existing QuantPilot session | The desk's auth. The arena UI does not add a League account and does not add Supabase. |
| `date-fns` + `date-fns-tz` | Market-time formatting (America/New_York) |
| `clsx` + `tailwind-merge` | Class utilities (shadcn standard) |
| `sonner` | Toast when a roster agent fills |
| `cmdk` | Command palette (search tickers, jump to pages) |
| `vaul` | Mobile bottom-sheet drawers |

### 4.4 Frontend graphics, effects and 3D

| Package | Use it for | Notes |
| --- | --- | --- |
| `three` | 3D engine | Peer dependency of the R3F packages below |
| `@react-three/fiber` | React renderer for three.js | v9 pairs with React 19 |
| `@react-three/drei` | Helpers: `OrbitControls`, `Float`, `Text`, `Html`, `Environment`, `useGLTF`, `Instances`, `Sparkles`, `Stars` | Use `Instances` for the market galaxy (500+ stocks) |
| `@react-three/postprocessing` | Bloom, vignette, chromatic aberration | Bloom on glowing nodes in the galaxy |
| `react-force-graph-3d` | 3D graph on a grounded trade | Built on three.js; load with `next/dynamic` and `ssr: false`. An ungrounded trade does not mount it |
| `react-force-graph-2d` | 2D fallback for mobile / low-power devices | Same data format as 3D |
| `lightweight-charts` | TradingView-style candlestick and line charts (price, equity curve) | Client-only; create the chart in `useEffect` |
| `recharts` | Lab comparisons: grounded vs ungrounded, return and drawdown | |
| `motion` | Animations: page transitions, list reordering on the leaderboard, layout animations | The package formerly called `framer-motion`; import from `motion/react` |
| `gsap` + `@gsap/react` | Scroll-driven landing page sequences (`ScrollTrigger`) | Use the `useGSAP` hook for cleanup |
| `@number-flow/react` | Animated equity and rank on the arena board | |
| `@tsparticles/react` + `@tsparticles/slim` | Particle background on the arena leaderboard | Use the slim bundle. Not a season finale |
| `@lottiefiles/dotlottie-react` | Small vector animations (agent "thinking", empty states) | Use free LottieFiles assets; check each asset's license |
| Magic UI and Aceternity UI components | Copy-paste effect components (animated borders, shimmer buttons, marquee, spotlight cards) | Installed through the shadcn CLI registry or copied manually; they rely on `motion` and Tailwind |

3D and effects guidelines:

- Landing hero — "Market Galaxy": each universe stock is an instanced sphere (`drei` `Instances`); position by sector cluster, size by market cap, color by today's % change (green/red), bloom on big movers. Max ~600 instances.
- Trade detail — "Why did this agent trade?": for a grounded rationale, `react-force-graph-3d` shows the path (thick links, other neighbors dimmed). A node opens a side panel with the company and the cited news. An ungrounded rationale shows the news and the flag `grounding=false`. It does not draw an empty graph.
- Performance: all 3D components are lazy-loaded with `next/dynamic` (`ssr: false`); cap device pixel ratio at 2 (`<Canvas dpr={[1, 2]}>`); fall back to 2D on mobile or when WebGL is unavailable.
- Accessibility: honor `prefers-reduced-motion` (disable particles and auto-rotation; use `motion`'s `useReducedMotion`). All numbers shown in 3D must also be available as text.

Removed from this section because nothing left uses them: `canvas-confetti`, and the trophy-room `.glb` models (`useGLTF` on `drei` stays, because the galaxy still uses `drei`). `next/og` was in section 4.2 and is removed with the recap cards. `zod`, `react-hook-form`, and `@hookform/resolvers` were only there for onboarding and agent settings, so they are not in section 4.3.

### 4.5 Infrastructure and deployment

| Piece | Choice |
| --- | --- |
| Local dev | `docker compose` with: postgres, redis, redpanda, neo4j, api, workers |
| Backend deploy | One small VM (e.g. DigitalOcean / Hetzner / AWS Lightsail) running the same compose file; Caddy for HTTPS |
| Neo4j | Neo4j AuraDB Free or the self-hosted container |
| Frontend deploy | Vercel |
| CI | GitHub Actions: ruff, mypy, pytest, `next lint`, `tsc --noEmit`, `next build` |
| Secrets | `.env` files locally, provider secret stores in deploy; never commit keys |

## 5. System architecture

```
Sources
  market.bars
  news.raw
  events.impact
  agent.cycle
  orders.proposed
        Massive 1-min aggregates (historical in Week 1)  News sources
        Market data service                              News ingest
                    Kafka / Redpanda
                    PySpark enrichment → impact events
                    Neo4j graph
                    Scheduler
                    Agent workers (LangGraph)
                    Simulator (risk engine + fills)
                    PostgreSQL          Redis
                    FastAPI (REST + WebSocket)
                    Next.js app (3D + charts)
```

### 5.1 Services (processes)

| Service | Responsibilities | Scale (v1) |
| --- | --- | --- |
| api | REST endpoints, WebSocket hub, auth verification | 1 process |
| marketdata | Week 2+: pull 1-min bars for the universe on a schedule, under the Massive rate limit; upsert every fetched session bar into `league.bars` (section 7.2); write the latest price to Redis; publish `market.bars`. No WebSocket in Week 1. | 1 |
| news-ingest | Existing QuantPilot ingest; publishes `news.raw` | 1 |
| enrichment | Existing PySpark job, extended to output `events.impact` (section 8.3) | 1 |
| scheduler | APScheduler: decision cycles each XNYS session, leaderboard snapshots, cycle metrics | 1 |
| agent-worker | Consumes cycle triggers; runs the LangGraph decision graph; publishes `orders.proposed` | 2–4 (horizontal) |
| simulator | Consumes `orders.proposed`; applies the risk engine; fills; writes Postgres; publishes `orders.filled`. `source = 'player'` still fills if a row exists. The arena does not produce those rows | exactly 1 (single writer for portfolios) |

### 5.2 Kafka topics

| Topic | Key | Value (JSON) | Producer → consumer |
| --- | --- | --- | --- |
| `market.bars` | ticker | `{ticker, t, o, h, l, c, v}` (1-min bar) | marketdata → simulator, enrichment |
| `news.raw` | news_id | `{id, source, url, title, body, published_at}` | news-ingest → enrichment |
| `news.enriched` | news_id | `{id, tickers[], themes[], sentiment, published_at}` | enrichment → agent-worker |
| `events.impact` | event_id | `{event_id, news_id, root_ticker, impact_score, affected:[{ticker, path_id, hops}]}` | enrichment → scheduler |
| `agent.cycle` | agent_id | `{agent_id, cycle_id, trigger: "scheduled"\|"event", event_id?}` | scheduler → agent-worker |
| `orders.proposed` | portfolio_id | `{order_id, portfolio_id, ticker, side, notional_usd, source: "agent", rationale_id}` | agent-worker → simulator |
| `orders.filled` | portfolio_id | `{order_id, fill_price, qty, filled_at}` | simulator → api (WebSocket fan-out) |

Keying by `portfolio_id` keeps all orders for one portfolio in order on one partition.

## 6. Data model (PostgreSQL)

All League tables live in schema `league`. Migrations live in `backend/alembic_league/` and record their revision in `league.alembic_version`. The env metadata is the League tables plus a one-column stub of `public.users`, so `players.user_id` resolves for `alembic check`. `include_object` ignores every object whose schema is not `league`, and the migration never alters `public`.

Both offline and online modes refuse before any SQL unless the URL's database name is `quantpilot` or `quantpilot_test`. Online mode then connects and also refuses unless `current_database()` is one of those two names and `public.users` exists. Only then does it create schema `league`. A localhost URL pointed at another database, such as `roadsafety`, is refused and leaves no `league` schema. League tests do not use the dev database. They read `LEAGUE_TEST_DATABASE_URL`, which defaults to `postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot_test`, and they refuse a URL whose database name is `quantpilot`. The QA downgrade and upgrade run on `quantpilot_test`, so they cannot drop `league.bars` in the dev database. There is still no `LEAGUE_DATABASE_URL`. Scripts such as the backfill and the replay use the app `DATABASE_URL`, which for local development is `quantpilot`.

League models declare `MetaData(schema="league")` and use the app's single engine through `SessionLocal` (`app.core.db.SessionLocal`, re-exported by `app.db.session`, psycopg2). There is no second engine and no `asyncpg`.

All League reads and writes go through `backend/app/league/repo/`. The fill engine and the risk engine are pure functions: values in, values out, no session. The simulator worker loads rows through the repository, calls those functions, and writes the results back through the repository.

Week 1 repository functions, all taking the caller's `Session` and none of them committing: `upsert_bars`, `first_bar_after`, `create_portfolio`, `get_portfolio`, `create_order`, `set_order_status`, and `record_fill(order_id, fill, commission)`. `record_fill` is the only public function that changes cash or positions. `_apply_fill` is private. Inside `session.begin_nested()` it locks the order, then the portfolio (`SELECT ... FOR UPDATE`, that order). Both selects set `populate_existing` so a row already in the session is overwritten with the committed values. It inserts the fill, sets `status` to `filled`, and applies the section 7.4 cash formulas. The caller commits. Before any cash math, `record_fill` quantizes `qty` to 6 decimal places with `ROUND_DOWN` for both buys and sells, and `price` to 4 decimal places with `ROUND_CEILING` on a buy and `ROUND_FLOOR` on a sell. If either quantized value is `<= 0`, it raises `FillNotPositive` and writes nothing. A raw `price <= 0` or `qty <= 0`, a `bar_time <= game_time`, and a negative commission (`InvalidCommission`) are also rejected before any write. Cash then uses those quantized inputs. Debits quantize to 4 decimal places with `ROUND_CEILING` and credits with `ROUND_FLOOR`. `positions.cost_basis` stores the unrounded product `qty * price` of those inputs. A buy adds that product. A partial sell sets `cost_basis` to `cost_basis * new_qty / old_qty`, kept at 10 decimal places. `avg_cost` is `cost_basis / qty`, `ROUND_HALF_EVEN` to 4 decimal places, recomputed from `cost_basis` on every fill. `filled`, `rejected`, `expired`, and `cancelled` are terminal. `set_order_status` may move `accepted` to `queued`, `rejected`, `expired`, or `cancelled`, and `queued` to `accepted`, `rejected`, `expired`, or `cancelled`. `process_order` locks the order the same way before it reads the status, and returns without writing when that status is no longer `accepted` or `queued`. Any other move raises `StatusTransitionNotAllowed`. Only `record_fill` may set `filled`. `create_order` does not accept `pending_approval`. Approve mode is not in the product. The status value stays in the check so the existing status tests still apply. A Hypothesis test of `record_fill` checks cash, quantity, and `avg_cost` on random buy and sell sequences. The default profile runs 20 examples. `HYPOTHESIS_PROFILE=thorough` runs 300. The test does not set its own example count, so the profile is what selects it.

`league.portfolios` is the paper cash book. It has nothing to do with `public.portfolios`, which is the existing watchlist.

All money is `NUMERIC(14,4)`. Quantities are `NUMERIC(18,6)` (fractional shares). All timestamps are `TIMESTAMPTZ` in UTC.

There is no `league.users` table and no Supabase user id.

The block below is migration 0001, including the arena columns. Tests rebuild `quantpilot_test` from this file. Gamification tables stay until a later cleanup migration.

```
league.players          (id UUID PK,
                         user_id INTEGER UNIQUE REFERENCES public.users(id),
                         display_name TEXT, avatar_id TEXT, xp INT DEFAULT 0,
                         created_at TIMESTAMPTZ)
league.leagues          (id UUID PK, name TEXT, is_public BOOL, invite_code TEXT UNIQUE NULL,
                         owner_id UUID FK players, max_members INT DEFAULT 50, created_at)
league.seasons          (id UUID PK, league_id UUID FK, number INT, starts_at TIMESTAMPTZ,
                         ends_at TIMESTAMPTZ, starting_cash NUMERIC(14,4) DEFAULT 1000,
                         status TEXT CHECK (status IN ('upcoming','active','finished')))
league.league_members   (league_id FK, player_id UUID FK players, joined_at,
                         PRIMARY KEY (league_id, player_id))
league.agents           (id UUID PK, player_id UUID FK players, league_id FK, name TEXT, risk_level TEXT,
                         style TEXT, llm_model TEXT NOT NULL DEFAULT 'unconfigured',
                         grounding_enabled BOOLEAN NOT NULL DEFAULT true,
                         interests TEXT[] DEFAULT '{}', excluded_tickers TEXT[] DEFAULT '{}',
                         excluded_sectors TEXT[] DEFAULT '{}', mode TEXT CHECK (mode IN ('autopilot','approve')),
                         config_version INT, created_at, UNIQUE (league_id, name))
league.agent_config_history (id UUID PK, agent_id FK, config JSONB, effective_from TIMESTAMPTZ)
league.portfolios       (id UUID PK, agent_id FK, season_id FK, kind TEXT CHECK (kind IN ('actual','ghost')),
                         cash NUMERIC(14,4) CHECK (cash >= 0), created_at,
                         UNIQUE (agent_id, season_id, kind))
league.positions        (portfolio_id FK, ticker TEXT, qty NUMERIC(18,6) CHECK (qty > 0),
                         avg_cost NUMERIC(14,4) CHECK (avg_cost >= 0),
                         cost_basis NUMERIC(28,10) NOT NULL CHECK (cost_basis >= 0),
                         updated_at, PRIMARY KEY (portfolio_id, ticker))
league.orders           (id UUID PK, portfolio_id FK, ticker TEXT, side TEXT CHECK (side IN ('buy','sell')),
                         notional_usd NUMERIC(14,4) NULL, qty NUMERIC(18,6) NULL,
                         CHECK ((notional_usd IS NULL OR notional_usd > 0)
                                AND (qty IS NULL OR qty > 0)
                                AND (notional_usd IS NOT NULL OR qty IS NOT NULL)),
                         source TEXT CHECK (source IN ('agent','player')),
                         status TEXT CHECK (status IN ('pending_approval','accepted','rejected',
                                'queued','filled','expired','cancelled')),
                         reject_reason TEXT NULL,
                         rationale_id UUID NULL FK rationales,
                         CHECK (source <> 'agent' OR rationale_id IS NOT NULL),
                         cycle_id UUID NULL,
                         created_at TIMESTAMPTZ, game_time TIMESTAMPTZ)
league.fills            (id UUID PK, order_id FK UNIQUE, price NUMERIC(14,4) CHECK (price > 0),
                         qty NUMERIC(18,6) CHECK (qty > 0),
                         slippage_bps NUMERIC(6,2), bar_time TIMESTAMPTZ, filled_at TIMESTAMPTZ)
league.rationales       (id UUID PK, cycle_id UUID NULL, summary TEXT, confidence NUMERIC(3,2),
                         news_ids TEXT[] DEFAULT '{}', graph_path JSONB, validated BOOL,
                         system BOOLEAN NOT NULL DEFAULT false,
                         grounding BOOLEAN NOT NULL DEFAULT true, created_at,
                         CHECK (confidence >= 0 AND confidence <= 1),
                         CHECK (system OR (cardinality(news_ids) > 0
                                AND array_position(news_ids, NULL) IS NULL
                                AND NOT ('' = ANY(news_ids)))),
                         CHECK (system OR grounding = false OR jsonb_array_length(graph_path) > 0),
                         CHECK (system = true OR cycle_id IS NOT NULL))
league.agent_cycles     (id UUID PK, agent_id FK, trigger TEXT, event_id TEXT NULL,
                         started_at, finished_at TIMESTAMPTZ NULL, status TEXT,
                         tokens_in INT DEFAULT 0, tokens_out INT DEFAULT 0,
                         cost_usd NUMERIC(8,4) DEFAULT 0,
                         latency_ms INT NULL,
                         proposals_made INT DEFAULT 0, proposals_rejected INT DEFAULT 0,
                         proposals_dropped INT DEFAULT 0,
                         rejection_reasons JSONB DEFAULT '[]', drop_reasons JSONB DEFAULT '[]',
                         error TEXT NULL)
league.equity_snapshots (portfolio_id FK, t TIMESTAMPTZ,
                         equity NUMERIC(14,4) CHECK (equity >= 0),
                         cash NUMERIC(14,4) CHECK (cash >= 0),
                         PRIMARY KEY (portfolio_id, t))
                         -- every 5 min during market hours + daily close
league.achievements     (id TEXT PK, name TEXT, description TEXT, xp INT, icon TEXT)
league.user_achievements(id UUID PK, player_id UUID FK players, achievement_id FK,
                         season_id FK NULL, unlocked_at,
                         UNIQUE (player_id, achievement_id, season_id) NULLS NOT DISTINCT)
league.bars             (ticker TEXT, t TIMESTAMPTZ,
                         o NUMERIC(14,4), h NUMERIC(14,4), l NUMERIC(14,4), c NUMERIC(14,4),
                         v NUMERIC(20,6), feed TEXT,
                         PRIMARY KEY (ticker, t))
```

`leagues.owner_id`, `league_members.player_id`, `agents.player_id`, and `user_achievements.player_id` reference `league.players(id)`. `players.user_id` is the only column that references `public.users(id)`. That foreign key does not change `public.users`.

`user_achievements.season_id` is nullable, so it cannot be part of the primary key (Postgres primary-key columns are `NOT NULL`). The row has its own `id UUID` primary key. Uniqueness is `UNIQUE (player_id, achievement_id, season_id) NULLS NOT DISTINCT`, which treats two rows with a null season as the same key. That clause exists in Postgres 15 and newer. The local Compose database reports `server_version` 16.15 (`PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2)`). RDS `engine_version` is `16`. A server older than 15 would use two partial unique indexes instead: one on `(player_id, achievement_id) WHERE season_id IS NULL`, and one on `(player_id, achievement_id, season_id) WHERE season_id IS NOT NULL`.

`league.rationales.system` defaults to false. Who may set `system = true` is an application rule: only the stop-loss path. That ban is tested when the agent code exists (week 2). The database does not enforce it. `cycle_id` is nullable. The database requires `system = true OR cycle_id IS NOT NULL`, so a stop-loss row can omit the cycle and an agent rationale cannot.

A non-system rationale must have at least one news id, and none of those ids may be null or blank. Postgres enforces that with `system OR (cardinality(news_ids) > 0 AND array_position(news_ids, NULL) IS NULL AND NOT ('' = ANY(news_ids)))`. That check stays. An ungrounded agent is non-system, so it still has to cite real news ids. `grounding=false` does not relax it.

`ck_rationales_graph_or_system` is `system OR grounding = false OR jsonb_array_length(graph_path) > 0`. `grounding` defaults to true, so a row that omits the flag still needs a non-empty path when `system` is false. A stop-loss (`system=true`) may have an empty path. An ungrounded rationale (`grounding=false`) may have an empty path. Checking each edge against Neo4j stays in application code (week 2), and only when `grounding` is true. `validated = true` on an agent rationale is also an application rule. The UI labels system trades "Auto stop-loss" and does not draw a graph for them.

`agent_cycles.finished_at` is nullable while the cycle is running. `tokens_in`, `tokens_out`, `proposals_made`, `proposals_rejected`, and `proposals_dropped` default to 0. `cost_usd` defaults to 0. `latency_ms` is null until the cycle finishes. `rejection_reasons` and `drop_reasons` default to `'[]'`. The text arrays `interests`, `excluded_tickers`, `excluded_sectors`, and `news_ids` default to `'{}'`. `agents.llm_model` defaults to `'unconfigured'`. `agents.grounding_enabled` defaults to true.

`league.bars` is the durable 1-minute bar store. The marketdata poller upserts each fetched bar with `ON CONFLICT (ticker, t) DO NOTHING`. The repository reads this table for fills (section 7.4). Redis does not store the bar history. `league.bars.v` is `NUMERIC(20,6)`, the same width as `public.market_bars.volume` after migration 0013. Massive sends `v` as a fractional number. The column keeps that fraction. A bar is not dropped because its volume is not a whole share.

Invariants enforced by Postgres:

- `portfolios.cash >= 0`. `equity_snapshots.cash >= 0` and `equity_snapshots.equity >= 0`.
- `positions.qty > 0`, `positions.avg_cost >= 0`, and `positions.cost_basis >= 0`. `record_fill` deletes a position whose quantity reaches 0. The database also rejects storing `qty <= 0`.
- `fills.price > 0` and `fills.qty > 0`. At most one fill row per order (`order_id` is unique).
- An order has `notional_usd`, `qty`, or both. Each value that is present is `> 0`.
- `rationales.confidence` is from 0 through 1.
- `source = 'agent'` requires `rationale_id`, and that id references `league.rationales`. A player order may omit it.

Invariants enforced by application code, not by a constraint:

- `status = 'filled'` exists only together with its fill row. Only `record_fill` sets that status, and only inside the savepoint that also inserts the fill and updates cash and the position. `filled`, `rejected`, `expired`, and `cancelled` do not change again. `create_order` and `set_order_status` reject `filled`.
- `source = 'agent'` implies the referenced rationale has `validated = true`. Week 2.
- Only the stop-loss path sets `rationales.system = true`. Week 2.
- Each `graph_path` element is a real Neo4j edge. Week 2, and only when `grounding` is true.
- The arena calls `require_configured_model` before a cycle. `llm_model` of `'unconfigured'` or blank raises `UnconfiguredModel`. The database default is allowed as a stored value. The scheduler is Week 2. The gate is tested now.
- A rationale's `grounding` flag equals that agent's `grounding_enabled`. The database does not check this. The Week 2 path that inserts rationales does, and that path gets the test. It is not built yet.

### 6.2 Arena columns in 0001

Applied in `backend/alembic_league/versions/0001_league_schema.py`. The test database is rebuilt from that file. Inserts that omit the new columns still succeed, because each new column has a server default. The model omits the column or sends the default. It does not send NULL over the default.

- `agents.llm_model TEXT NOT NULL DEFAULT 'unconfigured'`
- `agents.grounding_enabled BOOLEAN NOT NULL DEFAULT true`
- `rationales.grounding BOOLEAN NOT NULL DEFAULT true`
- `ck_rationales_graph_or_system`: `system OR grounding = false OR jsonb_array_length(graph_path) > 0`
- `UNIQUE (league_id, name)` named `uq_agents_league_name`. This replaced `UNIQUE (player_id, league_id)`, so one system player can hold the roster. Names are unique inside one league.
- `agent_cycles.latency_ms INTEGER NULL`
- `agent_cycles.proposals_made`, `proposals_rejected`, `proposals_dropped` `INTEGER NOT NULL DEFAULT 0`
- `agent_cycles.rejection_reasons` and `drop_reasons` `JSONB NOT NULL DEFAULT '[]'`

`achievements`, `user_achievements`, `league_members`, `leagues.invite_code`, `players.xp`, `players.avatar_id`, `portfolios.kind = 'ghost'`, `orders.source = 'player'`, and `pending_approval` stay. `test_metadata_contains_only_league_tables` asserts that table set. The fingerprint test asserts `uq_user_achievements_player_achievement_season` is `NULLS NOT DISTINCT`. A later cleanup migration drops the unused gamification objects, and only after those tests are rewritten on purpose.

The arena seed, when it is built, is one `public.users` row, one `league.players` row (`display_name` Arena, `xp` 0, a placeholder `avatar_id`), one `league.leagues` row (name Arena, `invite_code` NULL, that player as owner), and one `league.seasons` row (`status` active, `starting_cash` 1000, no rollover). Each roster row is one `league.agents` row on that player and league, plus one `actual` portfolio. No `league_members` row. No ghost portfolio.

Evaluation result tables are a Week 3 migration. Adding them to 0001 would change the table set the migration test asserts.

### 6.1 Existing auth (reused by the desk, not by the arena)

QuantPilot already authenticates `public.users`. League does not add a second account system.

- `POST /auth/register` stores `public.users` (integer id, email, bcrypt password hash) and a `public.portfolios` watchlist row named "Default".
- `POST /auth/login` checks the password and returns an HS256 JWT. The `sub` claim is that integer user id. Expiry comes from `JWT_EXPIRE_DAYS`.
- Callers send `Authorization: Bearer <token>`. `get_current_user` loads `public.users` by that id. The reserved anonymous email is not a signed-in user.
- The arena seed inserts one `public.users` row and one `league.players` row for the system player (section 6.2). The UI does not register a person and does not look up a player from a JWT.

Week 1 does not add League routes. The arena does not add a signup route.

## 7. Market simulator

### 7.1 Universe

Default contents: current S&P 500 constituents plus these ETFs: SPY, QQQ, DIA, IWM, XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC, SMH, TAN, ICLN.

The loader reads `UNIVERSE_PATH`. The default is `config/universe.csv`. Columns are exactly `ticker`, `name`, `sector`, and `market_cap`. A missing or extra column fails and the error names the columns. `market_cap` may be empty. The loader does not invent a market cap. The file is opened as `utf-8-sig`, and whitespace is stripped from headers and values. A ticker must match `^[A-Z][A-Z.]{0,5}$`, must not end with a dot, and is stored and looked up in that uppercase form. If the file is missing, the loader fails with an error that names the path. `load_universe` returns a frozen universe with lookup by ticker and by sector.

`backend/tests/fixtures/universe_small.csv` is the Week 1 fixture, about 25 liquid names across sectors. It includes AAPL, MSFT, NVDA, AMD, TSM, GOOGL, AMZN, META, JPM, XOM, UNH, TSLA, SPY, QQQ, XLK, and SMH. Tests and the one-day replay pass this path. It is not the arena universe. The arena universe is the file `scripts/build_universe.py` writes. That script is step 12 and is not built yet.

`scripts/build_universe.py` writes `config/universe.csv`:

- S&P 500 constituents and GICS sectors come from Wikipedia's "List of S&P 500 companies" table, read with `pandas.read_html`.
- The section 7.1 ETF list is appended.
- Tickers are normalized to Massive's symbol form. A hyphen becomes a dot (`BRK-B` → `BRK.B`).
- Each symbol is checked with Massive reference data (`GET /v3/reference/tickers/{ticker}`) through the same client as section 7.2. The script keeps a symbol only when Massive lists it as an active US stock or ETF. It calls no order method. This check is step 12, not step 10.
- Symbols that fail the check are printed and dropped.
- `market_cap` is left empty. The script does not look up or invent a market cap.
- It prints a summary: total tickers written, how many were dropped, and why.

The arena file is refreshed by running that script, not by editing prices by hand. Long-only. US stocks and ETFs only. No options, crypto, shorting, or margin.

### 7.2 Prices and the game clock

Market data is Massive (formerly Polygon). QuantPilot already calls it for daily bars and news. League does not add a second vendor and does not change `backend/app/market/` or `MassiveMarketAdapter` (that adapter is daily bars). The League wrapper is `backend/app/league/market/massive_data.py`. It uses the existing `SourceClient`.

Week 1 uses the free Stocks Basic plan: historical 1-minute aggregates, `GET /v2/aggs/ticker/{ticker}/range/1/minute/{from}/{to}`, 5 calls per minute. The same plan allows 2 years of history. Auth is `Authorization: Bearer` from `MARKET_API_KEY`. The base URL is `MARKET_BASE_URL` (default `https://api.massive.com`). The pace is `MARKET_REQUESTS_PER_MINUTE` (default 5). `SourceClient` waits between calls and retries HTTP 429 with backoff. A `Retry-After` longer than `SOURCE_MAX_RETRY_AFTER_SECONDS` is not retried. The key is not a League setting and is never printed.

A `next_url` is followed only when its host equals the host of `MARKET_BASE_URL`. A link to any other host is not requested. The wrapper logs a warning that names the refused host, keeps the bars already read, and stops paging. The URL itself is not logged. The API key stays on the `Authorization` header and is not sent to that other host.

`results[].t` is the Unix millisecond timestamp for the start of the aggregate window. Massive's custom-bars docs define it that way, and the existing daily adapter treats the same field as the start of the day (00:00 America/New_York). The wrapper stores that start instant in UTC. It does not move the timestamp to the end of the minute. A bar outside the XNYS session (section 7.3), `[open, close)`, is not written. Volume is stored as `NUMERIC(20,6)`, including a fractional `v`. A fractional volume does not drop the bar. `adjusted=false`. Prices are not invented.

The game clock is real time minus `DATA_DELAY_MINUTES`. The value stays configurable. `SystemClock` (`backend/app/league/sim/clock.py`) takes that delay and a wall-time function. The default function is the only `datetime.now(timezone.utc)` call in League code. Tests pass their own function. `ManualClock` (also `FixedClock`) can be set to a later time and advanced by a non-negative `timedelta`. An earlier `set` or a negative `advance` raises `ClockWentBackwards`. A naive datetime raises. Aware datetimes are converted to UTC. America/New_York is for market-hours display later, not for this clock.

`DATA_DELAY_MINUTES` defaults to 0. Week 1 replays historical bars on an injected clock, so there is no live delay to apply. Live play is week 2 or later: Massive Starter is 15 minutes delayed, and that is when `DATA_DELAY_MINUTES` becomes 15. There is no IEX feed and no `scripts/check_iex_latency.py`.

A bar timestamped `t` is the start of that minute. Its close is not known at `t`. The latest known price at clock time `now` is the close of the most recent bar with `t + 1 minute <= now`. `latest_known_prices` is that read. Risk, stop-loss, player checks, and equity all use it. The future price service uses the same rule: it does not publish a minute's close until that minute has completed. Fills are different and unchanged. A fill uses the bar's open, the first in-session bar with `t > T` and `t <= now`. The UI badge says "Massive prices" when the delay is 0, and "Prices delayed N min" otherwise.

Some minutes have no bar. The fill engine uses the first in-session bar after the order time, or the first in-session bar at or after the next open when the order was accepted while the market was closed (section 7.4). A missing minute is not filled by copying an earlier bar.

The live `marketdata` poller is not Week 1. When it exists, it pulls 1-minute bars for the universe on a schedule, staying under `MARKET_REQUESTS_PER_MINUTE`. For each fetched session bar it:

1. Upserts the bar into `league.bars` (`ON CONFLICT (ticker, t) DO NOTHING`).
2. Writes only the latest bar for that ticker to Redis: `price:{ticker}` → `{t, c}`. Redis is the UI and equity-display cache. It is not the fill source.

`scripts/backfill_bars.py --start --end --tickers` (or `--universe`) loads historical minute bars into `league.bars`. Later backtests use that table. A repeat run does not change rows already stored.

The Week 1 replay reads bars already stored in `league.bars`. It does not fetch and it does not invent a price. Massive data is licensed and this repo is public, so a dump of real bars is not committed. `backend/tests/fixtures/bars_one_day/` is gitignored. Tests of the replay use synthetic bars. `scripts/replay_day.py` refuses a date that is not an XNYS session, and it refuses a universe ticker that has no in-session bar, naming the ticker and pointing at `scripts/backfill_bars.py`.

Retention: keep every bar. Pruning or partitioning can be added later. Neither is part of v1.

### 7.3 Market hours

The calendar is the `exchange_calendars` library, exchange `XNYS`, used offline. It includes holidays and early closes. No Redis cache and no network call. League code implements the same protocol as the test `SessionCalendar`: `is_open`, `next_open`, and `trading_minutes`. The close instant is not open. A known early close is 2026-11-27, 13:00 America/New_York. Thanksgiving is closed. A normal session is 09:30–16:00 America/New_York. A date outside the range the library has loaded raises `CalendarUnavailable`. The calendar does not invent a session past that range.

Orders created outside market hours get status `queued` and fill at the first in-session bar at or after the next open. A bar after the close is not that bar.

### 7.4 Fill model (market orders)

The fill function is pure. It receives the order, the game clock, the already-loaded bars for that ticker, and the config numbers. It does not query Postgres or Massive. The repository loads `league.bars` and, on the fallback below, writes the Massive rows before calling the function a second time.

`decide_fill` in `backend/app/league/sim/fills.py` is that function. `process_order` in `backend/app/league/sim/process.py` locks the order, re-reads its status, and returns without writing when that status is no longer `accepted` or `queued`. It then loads the held quantity, calls `decide_fill`, then calls `record_fill` or `set_order_status`. It does not commit and it does not compute cash itself. Week 1 does not fetch Massive from either function. The fallback fetch below waits for the Massive wrapper.

Given an order accepted at game time `T`:

1. Every fill bar is inside a trading session: `is_open(bar.t)` is true. An order accepted while the market is open uses the first such bar with `t > T` and `t <= now`. The bar at exactly `T` is never used. An order accepted while the market is closed uses the first such bar with `t >= next_open(T)` and `t <= now`. A bar the lookup returns outside that window — `t <= T`, `t < next_open(T)` for a closed order, `t > now`, or not `is_open(t)` — is ignored. A post-close bar is never a fill, including for an order accepted at 15:59. Minutes with no row are skipped. `next_open(T)` returning `None` raises `CalendarUnavailable` and leaves the order untouched. `process_order` sets a queued order to `rejected` directly.
2. If no row matches and the poller may be lagging, the repository fetches that ticker's window from Massive once, upserts into `league.bars` (`ON CONFLICT DO NOTHING`), and calls the fill function once more. The fill function itself does not perform this fetch. That fetch is not part of Week 1.
3. Base price = that bar's open.
4. Slippage: buys pay `open × (1 + SLIPPAGE_BPS/10000)`, sells receive `open × (1 − SLIPPAGE_BPS/10000)`. Default `SLIPPAGE_BPS = 5`. The result uses the same 4-decimal buy `ROUND_CEILING` and sell `ROUND_FLOOR` as `record_fill`.
5. Commission is `COMMISSION_USD`, default 0. A buy changes cash by `cash -= qty * fill_price + COMMISSION_USD`. A sell changes cash by `cash += qty * fill_price - COMMISSION_USD`. `record_fill` applies those formulas. It quantizes the buy debit up and the sell credit down, to 4 decimal places. The risk engine resizes a buy whose total would exceed available cash before the fill runs. If `record_fill` still raises `InsufficientCash`, `process_order` sets the order to `rejected` with reason `insufficient cash incl. commission` and writes nothing else. `InsufficientPosition` becomes `rejected` with reason `no position`. A sell when the held quantity is already 0 is that same rejection, not an expiry. One test uses a non-zero commission.
6. Quantity: for notional buys, `qty = floor_to_6dp(notional / fill_price)`, then one quantum less if the product would still exceed the notional. For sells, the requested qty, capped at the held qty. A sell fraction, when the pure function is given one, is `floor_to_6dp(held * fraction)`. Computed qty at or below 0 expires with `size rounds to zero`. A rounded price at or below 0 expires with `price rounds to zero`.
7. If there is still no eligible bar once more than `FILL_TIMEOUT_MINUTES` of trading time have passed since `T` (default 30; a halt or a long gap with no bar), the order expires. Trading minutes are open-session time in `(T, now]`. Time outside a session, including overnight and after an early close, counts as zero. A bar at exactly the timeout still fills. At exactly the timeout with no bar, the order waits. Expiry starts on the next trading minute.

A unit test builds a bar series with a gap and asserts the fill uses the first bar that exists after `T`, not a synthesized bar inside the gap. An early-close session (09:30–13:00 America/New_York) stops the trading-minute count at 13:00, ignores a 13:05 bar, and carries the remaining timeout into the next session.

### 7.5 Orders accepted by the simulator

- Agent orders: notional USD (e.g. "buy $120 of AMD") or sell fraction of position (e.g. "sell 50% of AMD"). The risk engine converts "sell X% of position" into a quantity when it creates the order: `floor` to 6 decimal places of the shares held times that percent, never more than the shares held. `decide_fill` still accepts `fraction`. `process_order` does not: the order it sees already has a quantity.
- Player orders: notional USD or quantity. `validate_player_order` still accepts them. They are not exposed in the UI. The arena scheduler does not create them.
- Minimum order size: $5 for `apply_risk` and for player orders. A stop-loss sells the full quantity even when that is under $5. The check belongs to the risk engine, not the fill engine.

### 7.6 Corporate actions (v1)

Bars are fetched unadjusted (`adjusted=false`) so fills match real traded prices. Split handling for positions and backtests stays as this section describes.

- Splits: apply split adjustments to positions and `avg_cost` using Massive's split data (or a manual admin command if unavailable). Backtests use split-adjusted bars.
- Dividends: ignored in v1 (documented on the FAQ page).

## 8. The agent

### 8.1 Decision triggers

- Scheduled: three cycles per trading day at game-clock 10:00, 12:30, 15:30 America/New_York.
- Event-driven: when an `events.impact` message has `impact_score >= EVENT_IMPACT_THRESHOLD` (default 0.7) and any affected ticker is in the agent's interest subgraph. Max 3 event cycles per agent per day.
- Per-agent lock: at most one cycle running per agent (Redis lock `lock:agent:{id}`, TTL 120 s).

### 8.2 Interests → graph

Interests come from a fixed list of themes, each mapped to Neo4j `Theme` nodes:

`ai_chips`, `cloud_ai`, `ev_and_batteries`, `clean_energy`, `cybersecurity`, `fintech_payments`, `biotech`, `consumer_brands`, `gaming_entertainment`, `defense_aerospace`, `energy_oil_gas`, `semis_equipment`, `ecommerce`, `social_media`, `banks`

Interest subgraph for an agent = all `Company` nodes within 2 hops of its selected `Theme` nodes, minus exclusions, intersected with the universe.

Neo4j schema (extend QuantPilot's existing graph to match):

```
(:Company {ticker, name, sector, market_cap})
(:Theme {id, name})
(:Person {name})
(:Event {event_id, news_id, title, published_at, impact_score})
(:Company)-[:IN_THEME]->(:Theme)
(:Company)-[:SUPPLIES {since}]->(:Company)
(:Company)-[:CUSTOMER_OF]->(:Company)
(:Company)-[:COMPETES_WITH]->(:Company)
(:Company)-[:INVESTED_IN {amount_usd, date}]->(:Company)
(:Company)-[:PARTNERS_WITH {date}]->(:Company)
(:Event)-[:MENTIONS]->(:Company)
```

Every relationship has `valid_from` (date first known) so backtests can ignore relationships that were not yet known (section 11).

### 8.3 Impact events (enrichment extension)

For each enriched news item the PySpark job:

1. Links mentioned tickers → `root_ticker` (highest-salience mention).
2. Scores impact (0–1) from: source weight, sentiment magnitude, whether the root is large-cap, novelty (not a duplicate within 24 h).
3. Expands up to 3 hops in Neo4j from the root along `SUPPLIES`, `CUSTOMER_OF`, `COMPETES_WITH`, `PARTNERS_WITH`, `INVESTED_IN`, storing each path with an ID.
4. Publishes `events.impact`.

### 8.4 LangGraph decision graph

```
Trigger: schedule or news event
  → gather_context
  → analyze_second_order · LLM
  → propose_orders · LLM JSON
  → validate_rationale
       invalid, retry left → propose_orders
       invalid, no retries → no trade, log it
       valid → risk_check · deterministic Python
             → submit_orders
             → done
```

State schema (Pydantic):

```python
class CycleState(BaseModel):
    cycle_id: UUID
    agent_id: UUID
    game_time: datetime
    trigger: Literal["scheduled", "event"]
    event_id: str | None
    config: AgentConfig              # style, risk_level, llm_model, grounding_enabled, interests, exclusions
    portfolio: PortfolioSnapshot     # cash, positions with current prices, equity
    news: list[NewsItem]             # last 24h, filtered to interest subgraph, published_at <= game_time
    paths: list[GraphPath]           # candidate second-order paths from Neo4j
    proposals: list[OrderProposal] = []
    validation_errors: list[str] = []
    retries: int = 0
```

| Node | Type | What it does |
| --- | --- | --- |
| `gather_context` | Code | Loads config, portfolio, prices (Redis), and news (Postgres/Kafka store). Caps: 20 news items. When `grounding_enabled` is true, also loads up to 15 candidate paths (Cypher). When it is false, `paths` is empty and no Cypher query runs. |
| `analyze_second_order` | LLM | Given news + paths, writes a short analysis of which companies are likely helped/hurt and why. |
| `propose_orders` | LLM (structured output) | Returns JSON only, matching `OrderProposal[]` (below). May return an empty list ("hold"). |
| `validate_rationale` | Code | Checks every proposal (rules below). On failure, sends errors back to `propose_orders` once (`MAX_RETRIES = 1`). |
| `risk_check` | Code | Applies section 9 limits; resizes or drops orders. The LLM never decides final size. |
| `submit_orders` | Code | Writes `rationales` + `orders` on the actual portfolio and publishes `orders.proposed`. The rationale's `grounding` flag copies `grounding_enabled`. The arena does not write a ghost portfolio. |

`OrderProposal` schema (LLM output):

```json
{
  "ticker": "AMD",
  "action": "buy",
  "size_pct_of_equity": 12.0,
  "summary": "TSMC fab delay squeezes Nvidia supply; AMD uses Samsung capacity and may take share.",
  "news_ids": ["n_83jd2", "n_77aa1"],
  "graph_path": [
    {"from": "TSM", "rel": "SUPPLIES", "to": "NVDA"},
    {"from": "NVDA", "rel": "COMPETES_WITH", "to": "AMD"}
  ],
  "confidence": 0.62
}
```

`size_pct_of_equity` on a buy is the percent of equity to spend. On a sell it is the percent of the current position to sell.

Validation rules (all must pass):

1. `ticker` is in the universe, not excluded, and (for buys) inside the agent's interest subgraph.
2. Every `news_id` exists, has `published_at <= game_time`, and is within the last 72 hours.
3. Graph path. When `grounding_enabled` is true, `graph_path` is non-empty, ≤ 3 hops, ends at `ticker`, and every edge exists in Neo4j with `valid_from <= game_time` (one Cypher query per path). When `grounding_enabled` is false, the path may be empty and is not checked. A system stop-loss rationale may have an empty path and empty `news_ids` (section 6). The database allows that empty path when `grounding=false` or `system=true`.
4. When `grounding_enabled` is true, at least one cited news item mentions a company on the path. Not required when `system` is true, and not required when `grounding_enabled` is false.
5. `summary` ≤ 280 characters; `confidence` in [0, 1].
6. Sells only for tickers currently held.

A proposal that fails after the retry is dropped and logged (`agent_cycles.error`). No trade without a validated rationale.

### 8.5 Style behavior

| Style | Prompt emphasis | Extra deterministic rule |
| --- | --- | --- |
| `news_driven` | React to fresh, high-impact events and their second-order effects | Only trade on news < 24 h old |
| `momentum` | Favor tickers with positive 5-day and 20-day returns | Skip buys where 20-day return < 0 |
| `value_tilt` | Favor laggards in the interest set with positive news | Skip buys where 20-day return > +25% |
| `contrarian` | Look for overreactions to negative news | Only buy after a ≥ 5% drop in 3 days |

### 8.6 LLM usage and cost controls

- The roster names the model per agent (`agents.llm_model`). `require_configured_model` refuses `'unconfigured'` and a blank id before a cycle. `LLM_FAST_MODEL` and `LLM_STRONG_MODEL` are not a second hidden choice. There is no weekly-recap model.
- Week 2 adds a provider-agnostic call. The same cycle code can call a frontier model or an open-weight model (OpenRouter or a local Ollama). The roster column is the model id. The provider is chosen from that id. No provider client is built in this step.
- Temperature 0.2. Structured output via the provider's JSON/tool-calling mode.
- Per-agent daily token budget (`AGENT_DAILY_TOKEN_BUDGET`, default 60k). When exceeded, the agent holds until the next day.
- Every cycle logs tokens, cost, latency, proposals made, proposals rejected by validation (with reasons), and proposals dropped by risk (with reasons). Section 17. Those columns are on `agent_cycles` in migration 0001.
- Live cycles cache identical prompts in Redis for 10 minutes. The cache key is a hash of the model inputs: model id, roster config, game time, news ids, and the portfolio snapshot. The Evaluation Lab stores that response with the run so a second run with the same hash does not call the model (section 11).

## 9. Risk engine (deterministic)

| Limit | Conservative | Balanced | Aggressive |
| --- | --- | --- | --- |
| Max position size (% of equity) | 15% | 25% | 40% |
| Max number of positions | 10 | 8 | 6 |
| Min cash buffer (% of equity) | 20% | 10% | 0% |
| Max new buys per day | 2 | 4 | 6 |
| Max single order (% of equity) | 10% | 15% | 25% |
| Stop-loss per position (auto-sell) | −8% | −12% | −20% |
| ETFs allowed | Yes | Yes | Yes |

`apply_risk` is a pure function. It does not read the database or the clock. Equity is `cash + Σ qty × latest known price` (section 7.2). A missing price, or a price ≤ 0, for a held ticker or for a proposal ticker that was quoted raises `MissingPrice`. The function does not invent a price.

Processing order:

1. Invalid size. A buy with `size_pct_of_equity` ≤ 0, or a sell outside (0, 100], is dropped (`invalid size`).
2. Exclusions drop buys (`excluded`), by ticker or by the listing's sector. A sell of a held name is kept so the position can be closed. An excluded ticker that is also outside the universe is `excluded`.
3. A buy whose ticker is not in the universe is dropped (`not in universe`). A sell of a held ticker is kept even if that ticker has left the universe. A sell of a ticker that is not held is dropped (`not held`).
4. A buy outside the interest tickers is dropped (`outside interests`).
5. When `etfs_allowed` is false, an ETF buy (sector `ETF`) is dropped (`etf not allowed`). All three shipped levels allow ETFs.
6. A buy and a sell of the same ticker in one call: keep the sell and drop the buy (`conflicting proposals`). An earlier gate wins, so an excluded buy is `excluded`.
7. Sells run first, in input order. `sell X%` becomes `qty = floor` to 6 decimal places of the starting held quantity times `X%`. Later sells of that ticker are clipped so the total never exceeds the shares held (`clipped: exceeds held`, or `exceeds held` when nothing remains).
8. Buys run by confidence descending. A tie breaks by ticker, then by input order.
9. Clip the buy to `max_single_order_pct` of equity (`clipped: max order N%`).
10. Clip the buy so the existing market value (`qty × price`) plus earlier accepted buys of that ticker in this call plus this buy stay within `max_position_pct` (`clipped: max position N%`).
11. Clip the buy to current cash minus the cash buffer minus commission (`clipped: cash buffer`). Expected sell proceeds are not cash. A sell fills later, at a possibly lower price, or may expire. The sum of `notional + commission` over accepted buys stays within `cash − buffer`.
12. Drop a buy that opens a ticker not already held when the position count is at `max_positions` (`max positions`). A full sell in this call does not free a slot. The slot frees on a later call, once the position is actually gone. Adding to a held ticker does not take a slot.
13. Drop a buy once `buys_today` plus buys accepted in this call reach `max_new_buys_per_day` (`max buys per day`). Every accepted buy counts, including an add to a name already held.
14. Drop whatever remains below `MIN_ORDER_USD`. The reason is `below $5 minimum` when the request itself is under the default minimum, and the clip reason when a clip is what pushed it under.

Exactly at a cap is kept. One cent over is clipped down to the cap. A buy that is kept after a clip records the clip that set its size. The numbers are read from `RISK_LIMITS`. `MIN_ORDER_USD` and `COMMISSION_USD` come from settings.

Stop-loss: `check_stop_losses(positions, prices, risk_level)` is a pure function. It returns a sell of the full quantity where `price <= avg_cost × (1 − |stop|)`. The order is marked `system=true`, `validated=true`, summary `"Stop-loss at -X%"` for that level, with empty `graph_path` and `news_ids`. It does not apply the $5 minimum and it does not insert a rationale. The repository inserts the rationale. Agent and LLM code cannot set `system`. The UI labels the trade "Auto stop-loss". The simulator calls it every 5 minutes on the game clock. That schedule is not part of the pure function.

The connector that later submits these sells must not create a second stop-loss sell while one is already pending for that ticker. `check_stop_losses` does not see open orders, so that check belongs to the connector. The connector is not part of this step.

Player orders (`validate_player_order`): a buy must be in the universe (`not in universe`). A sell of a held ticker is allowed even if it has left the universe. The order ticker's price must exist and be > 0 (`MissingPrice` otherwise). Cash and no-shorting apply, plus the $5 minimum. A buy's notional plus commission must fit in current cash. The order is accepted or dropped. It is not resized. Position limits, the cash buffer, exclusions, and the interest set do not apply to players. This function stays. It is not exposed in the UI. The arena does not call it.

## 10. Scoring and the arena board

There is no XP, no level, no achievement, and no season award.

### 10.1 Metrics (per portfolio)

- Equity = cash + Σ(qty × latest known price). The price is the close of the newest bar with `t + 1 minute <= now` (section 7.2).
- Return = equity / starting_cash − 1.
- Excess return vs. SPY = return − SPY return over the same window. SPY's window return is the first session open to the last session close in that window, the same definition the one-day replay prints.
- Sharpe = mean(daily returns) / std(daily returns) × √252, risk-free = 0. Shown only after ≥ 5 trading days. If the standard deviation is 0, the value is omitted, not invented.
- Max drawdown = max over t of (peak equity up to t − equity_t) / peak.
- Trade count = filled orders.

### 10.2 Leaderboard

Redis sorted set `lb:arena` with score = return (higher is better). Tie-break by Sharpe, then by agent name.

Recomputed every 5 minutes during market hours and at the close. Pushed over the WebSocket. The daily close is also a row in `league.equity_snapshots`.

The board is the roster plus a SPY row. SPY is not an agent and has no portfolio. Its return is the benchmark in section 10.1. There is no ghost tab and no human tab.

## 11. Evaluation Lab

Week 3. Not built.

Input: the roster file, a start date, an end date, and a seed. The window is capped at 6 months for one run. Decision times are the section 8.1 clock: 10:00, 12:30, and 15:30 America/New_York on each XNYS session in the window, plus the event trigger when an impact event is inside the window.

Engine: the same decision graph, simulator, and risk engine, with an injected clock. Fills read `league.bars`. The bars come from `scripts/backfill_bars.py`. The run does not invent a price. The same fill rule applies: first in-session bar with `t > decision_time` and `t <= sim_time`. Marks use `latest_known_prices`.

No look-ahead:

- News filtered to `published_at <= sim_time`.
- Graph edges filtered to `valid_from <= sim_time`. A relationship that was not known yet is not a path.
- Fills use the first bar strictly after the decision time, and not after `sim_time`.
- A minute bar's close is used only once `t + 1 minute <= sim_time`.

Every config in the roster is run on that same window. The stored row per config is:

- return vs SPY
- Sharpe
- max drawdown
- trade count
- `validation_reject_rate`
- `unsupported_claim_rate`
- LLM cost per agent-day
- latency

`validation_reject_rate` = proposals failing any validation rule / proposals made. It is reported for grounded agents only. Those failures are blocked, so the order is not submitted.

`unsupported_claim_rate` is comparable across grounded and ungrounded agents. The lab runs shadow validation on every proposal in both modes. Grounded agents are blocked when a check fails. Ungrounded agents are only recorded. A proposal is unsupported when any cited news id is missing, published after the decision time, or older than 72 hours, or when none of the cited news mentions the traded ticker or a company linked to it in Neo4j within 3 hops. The evaluator checks that graph link. The agent does not. A missing graph path on an ungrounded proposal does not count as unsupported, because the evaluator looks up the link itself. Universe, exclusion, and "not held" failures count in `validation_reject_rate` and do not count as unsupported claims.

The Week 3 experiment is three configs on the same style and the same window: grounded open-weight, grounded frontier, and ungrounded frontier. The stored comparison is `unsupported_claim_rate`, return vs SPY, and cost per agent-day. Each grounded roster row still has an ungrounded twin for the same model. The results page shows the three-way comparison first.

Reproducible: the seed is stored on the run. An LLM response is cached by the hash from section 8.6. A rerun with the same hash reads the stored response and does not call the provider. The cache is part of the run, not only the 10-minute Redis entry.

Limitation, shown on the results page: the LLM may already know how a past event turned out, because that event can be in its training data. A backtest can look better than a live run for that reason. Prefer a window after the model's knowledge cutoff when the provider documents one. The page states the cutoff it used, or "not documented" when the provider does not.

Results live in a later migration, not in 0001: `league.evaluation_runs` and `league.evaluation_results`. The run row stores the seed, the roster hash, the window, and the model cutoff note. The result row stores one config's metrics. Those tables are Week 3. Adding them now would change the table set the migration test asserts.

## 11a. Backtesting note

Section 11 is the lab. The one-day replay in section 7.2 is the Week 1 check that the simulator holds its invariants on real bars. It is not the lab. It does not call an LLM.

## 12. API

All endpoints under `/api/v1`. Errors: `{ "error": { "code": "STRING", "message": "..." } }`. These routes are Week 4. They are read-only. There is no route that creates an agent, joins a league, places a player order, or approves an order.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/arena` | Roster ranked by return, with the SPY benchmark |
| GET | `/agents/{id}` | Config: style, risk, model, grounding, interests |
| GET | `/agents/{id}/equity?range=1d\|1w\|all` | Equity series plus the SPY series |
| GET | `/agents/{id}/orders?cursor=` | Orders with fills |
| GET | `/rationales/{id}` | Summary, news, graph path when grounded, and the grounding flag |
| GET | `/evaluation` | Stored lab runs |
| GET | `/evaluation/{id}` | One run, one row per config, twin pairs first |
| GET | `/market/universe` · `/market/quote/{ticker}` | Universe list, latest known price. The badge is "Massive prices" when `DATA_DELAY_MINUTES` is 0, and "Prices delayed N min" otherwise. |

WebSocket: `GET /ws`. No token. Client subscribes with `{"op":"sub","channels":["arena","agent:{id}","prices:{ticker}"]}`.

Server messages:

```json
{"type":"fill","agent_id":"...","portfolio_id":"...","order_id":"...","ticker":"AMD","side":"buy","qty":0.83,"price":144.21}
{"type":"equity","agent_id":"...","equity":1043.17,"t":"..."}
{"type":"leaderboard","top":[{"agent":"...","return":0.043,"rank":1}],"spy_return":0.01}
{"type":"price","ticker":"AMD","c":144.35,"t":"..."}
```

Redis pub/sub channels with the same names feed the WebSocket hub. The price on the socket is a completed minute (section 7.2), not the bar that is still forming.

### 12.1 Read-only MCP server

Week 5. Not built. Tools are `get_leaderboard`, `get_agent_portfolio`, `explain_trade`, and `get_eval_results`. Each one reads. None of them creates, modifies, or cancels an order, and none of them changes a config. A test lists the registered tools and fails if any tool writes. Claude, OpenClaw, and Hermes can query the arena through this server. The server does not accept a write tool added later without that test failing.

## 13. Security, fairness and compliance

- Paper money only. No broker trading endpoints are ever called. Massive is market data only. The calendar is a local library.
- Not investment advice. The footer says the arena is simulated research, not a recommendation.
- Rate limits (Redis): the read API is 120 requests/min per IP. There is no player-order route to limit.
- Only the simulator writes portfolios, positions, and fills (single writer), and it does so through `backend/app/league/repo/`. The marketdata poller is the writer for `league.bars`, also through that repository. Fill and risk functions do not hold a session.
- Validate every input with Pydantic. Tickers must match `^[A-Z][A-Z.]{0,5}$`, must not end with a dot, and must be in the universe.
- The arena stores no account email. The one system `public.users` row is a local bookkeeping row, not a person.
- Respect data-provider terms: do not redistribute raw market data feeds; show derived values in-app only.
- The roster is fixed for a session. Orders are timestamped on the game clock. There is no human config to lock.

## 14. Repository structure

The target layout. Existing QuantPilot packages stay where they are. Week 1 does not rename `frontend/` to `web/` or replace `backend/app/market` (that package is the event-study engine).

```
quantpilot/
├─ backend/
│  ├─ app/
│  │  ├─ api/            # FastAPI routers (v1), websocket hub
│  │  ├─ core/           # existing pydantic-settings; League settings are a new module
│  │  ├─ db/             # existing models and session; League uses its own MetaData
│  │  ├─ agent/          # graph.py (LangGraph), nodes/, prompts/, schemas.py, validate.py
│  │  ├─ graph/          # neo4j client, cypher queries, path validation
│  │  ├─ market/         # existing event-study engine; League does not modify this package
│  │  ├─ league/         # repo/, universe.py, sim/ (xnys.py), market/massive_data.py
│  │  ├─ backtest/       # runner, replay clock
│  │  └─ workers/        # entrypoints: scheduler, agent_worker, simulator, marketdata
│  ├─ tests/             # unit, property (hypothesis), integration
│  │  └─ fixtures/universe_small.csv
│  ├─ alembic_league/    # league schema only; version table league.alembic_version
│  └─ pyproject.toml
├─ pipelines/            # existing PySpark jobs + impact-event extension
├─ frontend/             # existing Next.js app; follows root DESIGN.md. Not renamed to web/
│  ├─ app/               # routes from section 3.1
│  ├─ components/
│  │  ├─ three/          # MarketGalaxy, GraphPath3D
│  │  ├─ charts/         # EquityChart (lightweight-charts), LabComparison (recharts)
│  │  ├─ effects/        # Particles, NumberTicker (Magic UI/Aceternity copies)
│  │  └─ ui/             # shadcn components
│  ├─ lib/               # api client, ws client; existing desk auth, no Supabase, no League account
│  └─ public/             # no trophy models
├─ config/universe.csv          # written by scripts/build_universe.py
├─ scripts/backfill_bars.py
├─ scripts/build_universe.py
├─ docker-compose.yml
└─ docs/QUANTPILOT_LEAGUE.md    # this spec. Root DESIGN.md stays the UI system.
```

Where a target path already exists and means something else (`backend/app/market`, `backend/app/graph`, `backend/app/db/models.py`, `frontend/`), new League modules do not overwrite those files. The Massive wrapper and the XNYS calendar live under `backend/app/league/`. They do not replace the event-study package `backend/app/market`. Neo4j path checks, when they are built, extend the existing `backend/app/graph` client instead of adding a second driver. That extension is not Week 1.

## 15. Configuration (environment variables)

```
# Data
DATA_DELAY_MINUTES=0
UNIVERSE_PATH=config/universe.csv
# Infra
DATABASE_URL=postgresql://...              REDIS_URL=redis://...
KAFKA_BOOTSTRAP=localhost:9092             NEO4J_URI=  NEO4J_USER=  NEO4J_PASSWORD=
# LLM
LLM_PROVIDER=anthropic|openai   LLM_FAST_MODEL=   LLM_STRONG_MODEL=   LLM_API_KEY=
AGENT_DAILY_TOKEN_BUDGET=60000
# Paper books
STARTING_CASH=1000   SLIPPAGE_BPS=5   COMMISSION_USD=0
FILL_TIMEOUT_MINUTES=30   EVENT_IMPACT_THRESHOLD=0.7   MAX_EVENT_CYCLES_PER_DAY=3
MIN_ORDER_USD=5
```

`DATA_DELAY_MINUTES=0` is the Week 1 default. Set it to 15 only when live play uses Massive Starter (section 7.2). There are no `ALPACA_*` settings. The Massive key is the existing app setting `MARKET_API_KEY`, with `MARKET_BASE_URL` and `MARKET_REQUESTS_PER_MINUTE` (default 5). League settings do not copy that key. `DATABASE_URL` is the app's sync URL. Local development uses `postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot`. The app opens one engine from it, and League uses `SessionLocal`. There is no `LEAGUE_DATABASE_URL`. League tests use `LEAGUE_TEST_DATABASE_URL`, default `postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot_test`. That name is a test environment variable, not an app setting. The QA downgrade runs there so it cannot drop bars in the dev database. There are no Supabase settings. `UNIVERSE_PATH` is the loader input (section 7.1). `SEASON_WEEKS` is still a field on `LeagueSettings` from Week 1. The arena does not read it and does not reset portfolios. `LLM_FAST_MODEL` and `LLM_STRONG_MODEL` are the same: still on the settings object, not what the roster uses. The roster column `llm_model` is the model id.

## 16. Testing strategy

| Area | Tests |
| --- | --- |
| Fill engine | Fills use the first existing `league.bars` row with `t > order_time` and `t <= game_clock`; a gap test (no synthesized bar inside a hole); slippage direction; fractional qty rounding; expiry on halt; one Massive fallback fetch then expiry |
| Risk engine | Each limit in section 9, per risk level, at the cap and one cent over; processing order; buys spend current cash only; player buys must be in the universe. Stop-loss at the threshold and one cent above. The pending-stop check is the connector's. Agent and LLM paths cannot set `rationales.system`. Only the stop-loss result may, and that row has empty `graph_path` and `news_ids` with `validated = true`. |
| Property tests (hypothesis) | For random order sequences: cash never < 0, qty never < 0, equity = cash + Σ positions, ghost and actual diverge only via player orders. The arena does not create a ghost book or a player order. The tests stay, because the simulator still implements both |
| No look-ahead | A minute bar's close is used only once `t + 1 minute <= now` (section 7.2). A lab run on synthetic data with a known future spike must not see it |
| Rationale validation | Database: an ungrounded rationale with an empty path is accepted (`test_ungrounded_rationale_accepts_an_empty_graph_path`); empty or blank news is still rejected (`test_ungrounded_rationale_still_rejects_empty_or_blank_news`); a grounded non-system rationale with an empty path is rejected (`test_grounded_nonsystem_rationale_rejects_an_empty_graph_path`). Neo4j edge checks are Week 2. The app rule that `rationales.grounding` equals `agents.grounding_enabled` is tested on the Week 2 insert path, which is not built. |
| Agent graph | LLM mocked with fixed JSON; retry path; empty proposals; one grounded cycle and its ungrounded twin. `require_configured_model` refuses `'unconfigured'` (`test_unconfigured_model_is_refused`). The cycle itself is Week 2. |
| API | Read-only arena and lab routes. No player-order route and no approve route. Week 4. Not tested yet |
| Frontend | Leaderboard renders the roster and the SPY row. A grounded trade mounts the 3D path. An ungrounded trade does not. Week 4. Not tested yet |

## 17. Observability

Structured logs (`structlog`) with `cycle_id`, `agent_id`, `order_id`.

From Week 2, every cycle writes:

- tokens in, tokens out, and cost in USD
- latency, from `started_at` to `finished_at`
- proposals made
- proposals rejected by validation, each with the rule that failed
- proposals dropped by risk, each with the section 9 reason

The log line and the `agent_cycles` row carry the same counts. Those columns are in migration 0001.

Week 2 also traces every cycle with LangSmith and OpenTelemetry. Both are already in the repo. The trace carries the prompt, the tool calls, the validation result, tokens, cost, and latency, and it is keyed by `cycle_id`. The trace is optional: with no keys it is a no-op. It never logs secrets. The Evaluation Lab reads these traces.

`/admin` is a read-only page on the same machine: cycles per hour, validation failure rate, LLM cost per day, fills per hour, WebSocket connections. It is not a player admin and it is not role-gated for a human league. Week 4.

Alerts: marketdata stale > 5 min during market hours; simulator lag > 2 min.

## 18. Build plan (5 weeks)

| Week | Build | Done when |
| --- | --- | --- |
| 1 | Simulator, done: `league` schema (0001 now includes the arena columns in section 6.2), the small universe fixture, game clock, Massive historical 1-minute backfill into `league.bars`, offline XNYS calendar, pure fill and risk functions, repository, property tests, and a one-day replay that reads `league.bars`. Real bars are never committed. Tests use synthetic bars on `quantpilot_test`. No live poller and no IEX check. Next, before Week 2: step 12, `scripts/build_universe.py`, the S&P 500 list from Massive reference data (`GET /v3/reference/tickers/{ticker}`), not a broker asset list. | The replay script places seeded orders for one day of bars already in `league.bars` and the invariants hold. `public` is unchanged. The replay does not fetch. Step 12 prints how many symbols it wrote and how many it dropped, and it does not invent a market cap. |
| 2 | The agent. News impact events, the interest subgraph, the LangGraph cycle, rationale validation against Neo4j, grounded and ungrounded modes, the provider-agnostic model call, LangSmith and OpenTelemetry traces keyed by `cycle_id`, the section 17 metrics, and the scheduler that runs the roster every XNYS session. The insert path checks that `rationales.grounding` equals `agents.grounding_enabled`. | One grounded cycle and its ungrounded twin trade on the same bars. The grounded trade has a validated path. The ungrounded trade has real news ids, an empty path, and `grounding=false`. Both went through `apply_risk`. The cycle row has tokens, cost, latency, and the reject and drop reasons. A trace exists when keys are set, and the same cycle is a no-op when they are absent. |
| 3 | Evaluation Lab. Backfill the historical window into `league.bars`. A runner executes every roster config on that window. The stored experiment is grounded open-weight vs grounded frontier vs ungrounded frontier, same style and window. Same seed and the input-hash cache make a second run reuse the LLM responses. | One stored run reports, per config, return vs SPY, Sharpe, max drawdown, trade count, `validation_reject_rate`, `unsupported_claim_rate`, LLM cost per agent-day, and latency. The three-way comparison is the first table. The page states the knowledge-cutoff caveat. A rerun with the same hash does not call the provider. |
| 4 | API, WebSocket, and the dashboard: arena leaderboard, per-agent equity vs SPY, trade feed, the 3D graph for a grounded trade, the lab results page, and the market-galaxy landing page. | The board moves when a fill is written. A grounded trade shows the path. An ungrounded trade shows the flag and does not mount the graph. |
| 5 | Deploy. The arena runs every trading day. The read-only MCP server (section 12.1). README and a findings write-up. A demo video. | The daily job is on a schedule. The MCP test fails if any tool writes. The write-up includes a losing window if there is one, and the video shows one grounded path and the lab table. |

## 19. Out of scope for v1

Real-money trading or broker integration · options, crypto, shorting, margin · dividends · mobile native apps · non-US markets · user-written strategies · pruning or partitioning of `league.bars`.

Also out of scope, removed from this product: human leagues, invite codes, league membership, player-facing seasons, achievements, XP, levels, a trophy room, approve mode, weekly recap cards, OG images, player onboarding, a player-order UI, and a launch that needs real users. `validate_player_order` stays in the simulator and is not exposed.

## 20. Metrics to report (resume / demo)

- Roster size, cycles run, trades filled
- `validation_reject_rate` for grounded agents, and `unsupported_claim_rate` across grounded open-weight, grounded frontier, and ungrounded frontier
- Return, Sharpe, and max drawdown vs SPY, including a window that loses
- p95 latency: event published → fill, and the cycle latency stored on `agent_cycles`
- LLM cost per agent-day

## 21. Disclaimers

QuantPilot League is a simulated research arena. It does not provide investment advice. Past or simulated performance does not predict future results. An LLM backtest can be optimistic when the model has already seen the events (section 11).
