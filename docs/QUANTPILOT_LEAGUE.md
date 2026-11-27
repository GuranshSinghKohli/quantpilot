# QuantPilot League — Design Doc

A fantasy league for AI trading agents. Every player gets $1,000 in paper money and an AI agent that trades for them based on their interests and risk style. Every trade is explained by a verifiable path through QuantPilot's knowledge graph, from a news event to the stock it affects.

Status: v1 design · Owner: Guransh Singh Kohli · Last updated: November 2026

This file is the League spec and the single source of truth. In League instructions, "DESIGN.md" means this file. Root `DESIGN.md` is the QuantPilot UI design system. It stays untouched. The League frontend reuses that file's colors, typography, spacing, and components. The 3D and effects libraries below are additions on top of it, not replacements.

Amendments recorded before Week 1 code, against the November 2026 PDF:

- All section 6 tables live in Postgres schema `league`. League migrations use their own Alembic version table, `league.alembic_version`, and never modify the `public` schema.
- There is no `league.users` and no Supabase Auth. `league.players` points at the existing `public.users` integer id. `league.portfolios` is the cash book, not the `public.portfolios` watchlist.
- League models use their own `MetaData(schema="league")` and the existing sync engine and session factory (`psycopg2`). No `asyncpg`. Database access goes through `backend/app/league/repo/`. Fill and risk are pure functions.
- `DATA_DELAY_MINUTES=0` when `ALPACA_DATA_FEED=iex`, and 15 for SIP without a subscription. `scripts/check_iex_latency.py` measures IEX bar age before that default is treated as final.
- One-minute bars are stored in `league.bars`. Redis keeps only the latest price. Fills use the first existing bar strictly after the order time.
- `league.rationales.system` defaults to false. Only the simulator may set it true. An empty graph path and empty news ids are allowed only then. The UI labels those trades "Auto stop-loss".
- Tests and the one-day replay read `backend/tests/fixtures/universe_small.csv`. `scripts/build_universe.py` writes `config/universe.csv`. The loader path is `UNIVERSE_PATH`.

## 0. How to use this document

This is the single source of truth for building QuantPilot League. Follow it exactly. Ask instead of guessing when something is not specified here.

1. Do not invent APIs, endpoints, or library functions. If a third-party API behaves differently from what is written here, the official docs win — update this file.
2. Every number in this file is a default, stored in config (section 15), never hard-coded.
3. Paper money only. Nothing in this codebase may place real orders with a broker.
4. Section 19 lists everything that is explicitly out of scope for v1. Do not build it.

## 1. Product summary

### 1.1 What it is

QuantPilot League extends the existing QuantPilot stock-intelligence platform (Kafka + PySpark news pipelines, Neo4j company knowledge graph, LangGraph research agents, FastAPI, PostgreSQL) into a multiplayer game:

- A player signs up, picks their interests (e.g. AI chips, clean energy), risk level, and style.
- The player gets an AI agent with a $1,000 paper portfolio inside a league (a group of players competing over a season).
- The agent trades automatically on real US market prices, using QuantPilot's news pipeline and knowledge graph.
- Every trade comes with an explanation card: a plain-English summary, the news articles it relied on, and the graph path (e.g. TSMC —SUPPLIES→ NVIDIA —COMPETES_WITH→ AMD), rendered as an interactive 3D graph.
- Players compete on a leaderboard, earn achievements, and can override their agent to see who does better: them or the AI.

### 1.2 What makes it different from "an LLM picks stocks"

| Typical AI trading project | QuantPilot League |
| --- | --- |
| LLM says "buy X" with a vague reason | Every trade cites real news IDs and a graph path that is validated against Neo4j before the trade is allowed |
| Fills at whatever price is convenient | Realistic simulator: market hours, next-bar fills, slippage, no look-ahead |
| LLM controls position size | Deterministic risk engine (plain Python) has the final say |
| Single user, demo only | Multiplayer leagues, leaderboards, seasons, real users |
| "It made money" claims | Honest backtests vs. SPY, including losing periods |

### 1.3 Target users (v1)

University students (start with Waterloo) who are curious about markets but don't want to risk real money.

## 2. Core concepts and glossary

| Term | Definition |
| --- | --- |
| Player | A signed-up user. |
| League | A group of players competing in one season. Can be public (global) or private (invite code). |
| Season | A fixed time window for a league (default 4 weeks). Portfolios reset each season. |
| Agent | The AI trader owned by one player in one league. Has a config (interests, risk, style). |
| Portfolio | Cash + positions for one agent in one season. Starts at $1,000.00 cash. |
| Ghost portfolio | A shadow copy of the portfolio that only follows the agent's decisions, ignoring player overrides. Used for "You vs. your agent". |
| Decision cycle | One run of the agent: gather context → analyze → propose orders → risk check → execute → explain. |
| Order | A request to buy/sell a ticker. v1 supports market orders only. |
| Fill | The simulated execution of an order at a specific price and time. |
| Rationale | The explanation attached to every agent order: summary, evidence (news IDs), graph path, confidence. |
| Universe | The set of tradable tickers (default: S&P 500 constituents + a fixed ETF list, US only). |
| Game clock | The time the simulator considers "now". Equals real time minus `DATA_DELAY_MINUTES` (section 7.2). |

## 3. User experience (screens and flows)

### 3.1 Screens

| Route | Screen | Purpose |
| --- | --- | --- |
| `/` | Landing | 3D "market galaxy" hero, pitch, sign-up CTA |
| `/onboarding` | Agent builder | 4 steps: interests → risk → style → name + avatar |
| `/dashboard` | Home | Portfolio value (animated), equity curve vs. SPY, positions, latest trades feed |
| `/trades/[id]` | Trade detail | Explanation card + 3D graph path + news evidence |
| `/leagues` | Leagues list | Join public league, create/join private league via code |
| `/leagues/[id]` | League page | Leaderboard (live), season countdown, members |
| `/agent` | Agent settings | Edit config (locked fields during a season: see 3.3), autopilot vs. approve mode |
| `/versus` | You vs. your agent | Actual portfolio vs. ghost portfolio chart |
| `/achievements` | Trophy room | 3D trophies, XP, level |
| `/recap/[seasonId]/[week]` | Weekly recap | Shareable summary card (OG image) |
| `/backtest` | Backtest lab | Run your agent config on a past window, compare vs. SPY |

### 3.2 Onboarding flow

1. Interests — pick 1–5 themes from a fixed list (section 8.2). Each theme maps to nodes in Neo4j.
2. Risk level — `conservative`, `balanced`, `aggressive` (maps to hard limits, section 9).
3. Style — `news_driven`, `momentum`, `value_tilt`, `contrarian` (changes the prompt and scoring, section 8.4).
4. Name + avatar — agent name (3–24 chars) and one of the preset 3D avatars.
5. Optional exclusions — tickers or sectors the agent must never buy.
6. Player is placed in the current public league season (or joins a private one by code).

### 3.3 Config changes mid-season

- Interests and style: may be changed once per week; change takes effect at the next trading day open.
- Risk level: locked for the season (prevents gaming the leaderboard).
- Mode (autopilot vs. approve): can change any time.

### 3.4 Modes

- Autopilot (default): agent orders execute automatically after the risk check.
- Approve mode: agent orders become `pending_approval`; the player has 30 minutes (game clock) to approve or reject; unapproved orders expire.
- Manual override: the player can place their own market orders anytime. These affect the real portfolio but not the ghost portfolio.

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

League models use `MetaData(schema="league")` and the session factory already used by the app (`app.db.session`). They do not open their own engine. `asyncpg` is not a dependency. The League URL is the app's current `DATABASE_URL`.

### 4.2 New backend components

| Component | Technology | Why |
| --- | --- | --- |
| Market data | Alpaca Market Data API via official SDK `alpaca-py` | Free plan: historical bars (7+ years), IEX real-time WebSocket (max 30 symbols on the free plan), 200 REST calls/min. See 7.2 for the delay rule. |
| Market calendar | Alpaca Trading API `/v2/calendar` and `/v2/clock` (paper account keys) | Market open/close, holidays, early closes |
| News (optional extra source) | Alpaca News API (verify plan limits in the docs before relying on it) + existing QuantPilot sources | More news coverage |
| Cache / leaderboard / pub-sub | Redis (`redis` Python package, asyncio API) | Latest prices, sorted-set leaderboards, pub/sub to the WebSocket hub, rate limits |
| Scheduler | APScheduler (`apscheduler`, `AsyncIOScheduler`) | Decision cycles, leaderboard snapshots, season rollover |
| Kafka client | `confluent-kafka` (or `aiokafka`) | Produce/consume from Python services |
| Auth | Existing QuantPilot auth (section 6.1) | League routes reuse `public.users` and the current bearer JWT. Not wired in Week 1. |
| Analytics/maths | pandas, numpy | Metrics (returns, Sharpe, drawdown), backtests |
| HTTP client | httpx | External calls |
| Logging | structlog | Structured logs with `cycle_id`, `agent_id` |
| Testing / lint | pytest, pytest-asyncio, hypothesis, ruff, mypy | Quality |
| OG images | Next.js `ImageResponse` from `next/og` | Shareable recap cards |

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
| `zod` + `react-hook-form` + `@hookform/resolvers` | Forms + validation (onboarding, agent settings) |
| Existing QuantPilot session | Auth. The League UI does not add Supabase. |
| `date-fns` + `date-fns-tz` | Market-time formatting (America/New_York) |
| `clsx` + `tailwind-merge` | Class utilities (shadcn standard) |
| `sonner` | Toast notifications ("Your agent bought 2.1 AMD") |
| `cmdk` | Command palette (search tickers, jump to pages) |
| `vaul` | Mobile bottom-sheet drawers |

### 4.4 Frontend graphics, effects and 3D

| Package | Use it for | Notes |
| --- | --- | --- |
| `three` | 3D engine | Peer dependency of the R3F packages below |
| `@react-three/fiber` | React renderer for three.js | v9 pairs with React 19 |
| `@react-three/drei` | Helpers: `OrbitControls`, `Float`, `Text`, `Html`, `Environment`, `useGLTF`, `Instances`, `Sparkles`, `Stars` | Use `Instances` for the market galaxy (500+ stocks) |
| `@react-three/postprocessing` | Bloom, vignette, chromatic aberration | Bloom on glowing nodes in the galaxy and trophies |
| `react-force-graph-3d` | 3D knowledge-graph explorer on the trade detail page | Built on three.js; load with `next/dynamic` and `ssr: false` |
| `react-force-graph-2d` | 2D fallback for mobile / low-power devices | Same data format as 3D |
| `lightweight-charts` | TradingView-style candlestick and line charts (price, equity curve) | Client-only; create the chart in `useEffect` |
| `recharts` | Simple charts: sector allocation donut, win-rate bars, backtest comparisons | |
| `motion` | Animations: page transitions, list reordering on the leaderboard, layout animations | The package formerly called `framer-motion`; import from `motion/react` |
| `gsap` + `@gsap/react` | Scroll-driven landing page sequences (`ScrollTrigger`) | Use the `useGSAP` hook for cleanup |
| `@number-flow/react` | Smooth animated numbers for portfolio value and rank changes | |
| `canvas-confetti` | Confetti on achievement unlock / season win | Respect reduced motion |
| `@tsparticles/react` + `@tsparticles/slim` | Particle background on leaderboard / season finale | Use the slim bundle |
| `@lottiefiles/dotlottie-react` | Small vector animations (agent "thinking", empty states) | Use free LottieFiles assets; check each asset's license |
| Magic UI and Aceternity UI components | Copy-paste effect components (animated borders, shimmer buttons, marquee, spotlight cards) | Installed through the shadcn CLI registry or copied manually; they rely on `motion` and Tailwind |

3D and effects guidelines:

- Landing hero — "Market Galaxy": each universe stock is an instanced sphere (`drei` `Instances`); position by sector cluster, size by market cap, color by today's % change (green/red), bloom on big movers. Max ~600 instances.
- Trade detail — "Why did my agent buy this?": `react-force-graph-3d` showing the rationale's graph path highlighted (thick glowing links, other neighbors dimmed). Clicking a node opens a side panel with the company and linked news.
- Trophy room: 3D trophies as `.glb` models loaded with `useGLTF` (use CC0 models, e.g. from Poly Pizza or Kenney; record the license in `/public/models/LICENSES.md`).
- Performance: all 3D components are lazy-loaded with `next/dynamic` (`ssr: false`); cap device pixel ratio at 2 (`<Canvas dpr={[1, 2]}>`); fall back to 2D on mobile or when WebGL is unavailable.
- Accessibility: honor `prefers-reduced-motion` (disable confetti, particles, auto-rotation; use `motion`'s `useReducedMotion`). All numbers shown in 3D must also be available as text.

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
        Alpaca market data (1-min bars + IEX stream)     News sources
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
| marketdata | Pull 1-min bars for the universe on a schedule; subscribe to the IEX stream for up to 30 hot symbols; upsert every fetched bar into `league.bars` (section 7.2); write the latest price to Redis; publish `market.bars` | 1 |
| news-ingest | Existing QuantPilot ingest; publishes `news.raw` | 1 |
| enrichment | Existing PySpark job, extended to output `events.impact` (section 8.3) | 1 |
| scheduler | APScheduler: triggers decision cycles, leaderboard snapshots, metrics, season rollover | 1 |
| agent-worker | Consumes cycle triggers; runs the LangGraph decision graph; publishes `orders.proposed` | 2–4 (horizontal) |
| simulator | Consumes `orders.proposed` and player orders; applies the risk engine; fills; writes Postgres; publishes `orders.filled` | exactly 1 (single writer for portfolios) |

### 5.2 Kafka topics

| Topic | Key | Value (JSON) | Producer → consumer |
| --- | --- | --- | --- |
| `market.bars` | ticker | `{ticker, t, o, h, l, c, v}` (1-min bar) | marketdata → simulator, enrichment |
| `news.raw` | news_id | `{id, source, url, title, body, published_at}` | news-ingest → enrichment |
| `news.enriched` | news_id | `{id, tickers[], themes[], sentiment, published_at}` | enrichment → agent-worker |
| `events.impact` | event_id | `{event_id, news_id, root_ticker, impact_score, affected:[{ticker, path_id, hops}]}` | enrichment → scheduler |
| `agent.cycle` | agent_id | `{agent_id, cycle_id, trigger: "scheduled"\|"event", event_id?}` | scheduler → agent-worker |
| `orders.proposed` | portfolio_id | `{order_id, portfolio_id, ticker, side, notional_usd, source: "agent"\|"player", rationale_id?}` | agent-worker/api → simulator |
| `orders.filled` | portfolio_id | `{order_id, fill_price, qty, filled_at}` | simulator → api (WebSocket fan-out) |

Keying by `portfolio_id` keeps all orders for one portfolio in order on one partition.

## 6. Data model (PostgreSQL)

All League tables live in schema `league`. Migrations live in `backend/alembic_league/` and record their revision in `league.alembic_version`. That env's metadata is only the League `MetaData`. It never alters `public`.

League models declare `MetaData(schema="league")` and use the existing sync engine and session factory (`app.db.session`, psycopg2). There is no second engine and no `asyncpg`.

All League reads and writes go through `backend/app/league/repo/`. The fill engine and the risk engine are pure functions: values in, values out, no session. The simulator worker loads rows through the repository, calls those functions, and writes the results back through the repository.

`league.portfolios` is the paper cash book. It has nothing to do with `public.portfolios`, which is the existing watchlist.

All money is `NUMERIC(14,4)`. Quantities are `NUMERIC(18,6)` (fractional shares). All timestamps are `TIMESTAMPTZ` in UTC.

There is no `league.users` table and no Supabase user id.

```
league.players          (id UUID PK,
                         user_id INTEGER UNIQUE REFERENCES public.users(id),
                         display_name TEXT, avatar_id TEXT, xp INT DEFAULT 0,
                         created_at TIMESTAMPTZ)
league.leagues          (id UUID PK, name TEXT, is_public BOOL, invite_code TEXT UNIQUE NULL,
                         owner_id UUID FK players, max_members INT DEFAULT 50, created_at)
league.seasons          (id UUID PK, league_id UUID FK, number INT, starts_at TIMESTAMPTZ,
                         ends_at TIMESTAMPTZ, starting_cash NUMERIC DEFAULT 1000,
                         status TEXT CHECK (status IN ('upcoming','active','finished')))
league.league_members   (league_id FK, player_id UUID FK players, joined_at,
                         PRIMARY KEY (league_id, player_id))
league.agents           (id UUID PK, player_id UUID FK players, league_id FK, name TEXT, risk_level TEXT,
                         style TEXT, interests TEXT[], excluded_tickers TEXT[],
                         excluded_sectors TEXT[], mode TEXT CHECK (mode IN ('autopilot','approve')),
                         config_version INT, created_at, UNIQUE (player_id, league_id))
league.agent_config_history (id, agent_id FK, config JSONB, effective_from TIMESTAMPTZ)
league.portfolios       (id UUID PK, agent_id FK, season_id FK, kind TEXT CHECK (kind IN ('actual','ghost')),
                         cash NUMERIC(14,4), created_at, UNIQUE (agent_id, season_id, kind))
league.positions        (portfolio_id FK, ticker TEXT, qty NUMERIC(18,6), avg_cost NUMERIC(14,4),
                         updated_at, PRIMARY KEY (portfolio_id, ticker))
league.orders           (id UUID PK, portfolio_id FK, ticker TEXT, side TEXT CHECK (side IN ('buy','sell')),
                         notional_usd NUMERIC(14,4) NULL, qty NUMERIC(18,6) NULL,
                         source TEXT CHECK (source IN ('agent','player')),
                         status TEXT CHECK (status IN ('pending_approval','accepted','rejected',
                                'queued','filled','expired','cancelled')),
                         reject_reason TEXT NULL, rationale_id UUID NULL, cycle_id UUID NULL,
                         created_at TIMESTAMPTZ, game_time TIMESTAMPTZ)
league.fills            (id UUID PK, order_id FK UNIQUE, price NUMERIC(14,4), qty NUMERIC(18,6),
                         slippage_bps NUMERIC(6,2), bar_time TIMESTAMPTZ, filled_at TIMESTAMPTZ)
league.rationales       (id UUID PK, cycle_id UUID, summary TEXT, confidence NUMERIC(3,2),
                         news_ids TEXT[], graph_path JSONB, validated BOOL,
                         system BOOLEAN NOT NULL DEFAULT false, created_at)
league.agent_cycles     (id UUID PK, agent_id FK, trigger TEXT, event_id TEXT NULL,
                         started_at, finished_at, status TEXT, tokens_in INT, tokens_out INT,
                         cost_usd NUMERIC(8,4), error TEXT NULL)
league.equity_snapshots (portfolio_id FK, t TIMESTAMPTZ, equity NUMERIC(14,4), cash NUMERIC(14,4),
                         PRIMARY KEY (portfolio_id, t))
                         -- every 5 min during market hours + daily close
league.achievements     (id TEXT PK, name TEXT, description TEXT, xp INT, icon TEXT)
league.user_achievements(id UUID PK, player_id UUID FK players, achievement_id FK,
                         season_id FK NULL, unlocked_at,
                         UNIQUE (player_id, achievement_id, season_id) NULLS NOT DISTINCT)
league.bars             (ticker TEXT, t TIMESTAMPTZ,
                         o NUMERIC(14,4), h NUMERIC(14,4), l NUMERIC(14,4), c NUMERIC(14,4),
                         v BIGINT, feed TEXT,
                         PRIMARY KEY (ticker, t))
```

`leagues.owner_id`, `league_members.player_id`, `agents.player_id`, and `user_achievements.player_id` reference `league.players(id)`. `players.user_id` is the only column that references `public.users(id)`. That foreign key does not change `public.users`.

`user_achievements.season_id` is nullable, so it cannot be part of the primary key (Postgres primary-key columns are `NOT NULL`). The row has its own `id UUID` primary key. Uniqueness is `UNIQUE (player_id, achievement_id, season_id) NULLS NOT DISTINCT`, which treats two rows with a null season as the same key. That clause exists in Postgres 15 and newer. This project's database is Postgres 16: the Compose image is `pgvector/pgvector:pg16`, and RDS `engine_version` is `16`. Docker was not running, so this was taken from those declared versions rather than a live `SHOW server_version`. A server older than 15 would use two partial unique indexes instead: one on `(player_id, achievement_id) WHERE season_id IS NULL`, and one on `(player_id, achievement_id, season_id) WHERE season_id IS NOT NULL`.

`league.rationales.system` defaults to false. Only the simulator, through the risk engine, may insert a row with `system = true` (a stop-loss sell). Agent and LLM code paths must not set it. A test covers that ban.

An empty `graph_path` and empty `news_ids` are allowed only when `system = true`. Every `system = false` rationale must pass the section 8.4 checks. A system rationale is inserted with `validated = true`, so the "every agent order has a validated rationale" invariant still holds. The UI labels these trades "Auto stop-loss" and does not draw a graph for them.

`league.bars` is the durable 1-minute bar store. The marketdata poller upserts each fetched bar with `ON CONFLICT (ticker, t) DO NOTHING`. The repository reads this table for fills (section 7.4). Redis does not store the bar history.

Invariants (enforced in the simulator and tested):

- `portfolios.cash >= 0` always (no margin).
- `positions.qty > 0` (a position at 0 is deleted; no shorting).
- Every `filled` order has exactly one `fills` row.
- Every `source='agent'` order has a `rationale_id` whose `validated = true`.

### 6.1 Existing auth (reused later, not in Week 1)

QuantPilot already authenticates `public.users`. League does not add a second account system.

- `POST /auth/register` stores `public.users` (integer id, email, bcrypt password hash) and a `public.portfolios` watchlist row named "Default".
- `POST /auth/login` checks the password and returns an HS256 JWT. The `sub` claim is that integer user id. Expiry comes from `JWT_EXPIRE_DAYS`.
- Callers send `Authorization: Bearer <token>`. `get_current_user` loads `public.users` by that id. The reserved anonymous email is not a signed-in user.
- A later League route uses that same dependency, then loads `league.players` where `user_id` equals `public.users.id`.

Week 1 does not add League routes and does not create player rows from signup.

## 7. Market simulator

### 7.1 Universe

Default contents: current S&P 500 constituents plus these ETFs: SPY, QQQ, DIA, IWM, XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC, SMH, TAN, ICLN.

The loader reads `UNIVERSE_PATH`. The default is `config/universe.csv`. Columns are `ticker, name, sector, market_cap`. `market_cap` may be empty. The loader does not invent a market cap. If the file is missing, the loader fails with an error that names the path.

`backend/tests/fixtures/universe_small.csv` is the Week 1 fixture, about 25 liquid names across sectors. It includes AAPL, MSFT, NVDA, AMD, TSM, GOOGL, AMZN, META, JPM, XOM, UNH, TSLA, SPY, QQQ, XLK, and SMH. Tests and the one-day replay pass this path. It is not the season universe.

`scripts/build_universe.py` writes `config/universe.csv`:

- S&P 500 constituents and GICS sectors come from Wikipedia's "List of S&P 500 companies" table, read with `pandas.read_html`.
- The section 7.1 ETF list is appended.
- Tickers are normalized to Alpaca's symbol form. A hyphen becomes a dot (`BRK-B` → `BRK.B`).
- Each symbol is checked with `TradingClient.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))` from `alpaca-py`. The client is constructed for the paper account. The script keeps a symbol only when the returned asset is `tradable`. It calls no order method.
- Symbols that fail the check are printed and dropped.
- `market_cap` is left empty. The script does not look up or invent a market cap.
- It prints a summary: total tickers written, how many were dropped, and why.

The season file is refreshed by running that script, not by editing prices by hand. Long-only. US stocks and ETFs only. No options, crypto, shorting, or margin.

### 7.2 Prices and the game clock

Checked against Alpaca's market-data docs (November 2026):

- The Basic plan includes real-time IEX data. The IEX WebSocket is limited to 30 symbols.
- Historical SIP queries without a subscription require `end` to be at least 15 minutes in the past.
- That 15-minute cutoff is a SIP rule. It is not the rule for the IEX feed.

The game clock is real time minus `DATA_DELAY_MINUTES`. The value stays configurable.

- `ALPACA_DATA_FEED=iex` (the section 15 default): `DATA_DELAY_MINUTES` defaults to 0.
- `ALPACA_DATA_FEED=sip` with no SIP subscription: `DATA_DELAY_MINUTES` is 15.

All decisions and fills use bars at or before the game clock. The UI badge says "IEX prices" when the delay is 0, and "Prices delayed N min" otherwise.

`scripts/check_iex_latency.py` runs during market hours. It requests the latest IEX bars for SPY, AAPL, and one small S&P 500 stock, and prints how old each bar is. If those bars are delayed, set `DATA_DELAY_MINUTES` back to 15. The script calls no order method.

IEX is a small share of total volume, so some tickers will have minutes with no bar. The fill engine uses the first bar that exists strictly after the order time (section 7.4). A missing minute is not filled by copying an earlier bar.

`marketdata` pulls 1-minute bars for the whole universe every minute (batched multi-symbol requests, staying under the REST rate limit). For each fetched bar it:

1. Upserts the bar into `league.bars` (`ON CONFLICT (ticker, t) DO NOTHING`).
2. Writes only the latest bar for that ticker to Redis: `price:{ticker}` → `{t, c}`. Redis is the UI and equity-display cache. It is not the fill source.

`scripts/backfill_bars.py --start --end --tickers` loads historical bars into `league.bars`. Later backtests use that table.

The Week 1 replay does not wait on a live backfill. It loads `backend/tests/fixtures/bars_one_day/`, one real trading day of 1-minute bars for the fixture tickers, into `league.bars`. That fixture is produced once from `scripts/backfill_bars.py`. Prices in it are not invented.

Retention: keep every bar. Pruning or partitioning can be added later. Neither is part of v1.

### 7.3 Market hours

Use Alpaca `/v2/calendar` to load trading days (including early closes) once per day, cached in Redis.

Orders created outside market hours get status `queued` and fill at the first bar after the next open.

### 7.4 Fill model (market orders)

The fill function is pure. It receives the order, the game clock, the already-loaded bars for that ticker, and the config numbers. It does not query Postgres or Alpaca. The repository loads `league.bars` and, on the fallback below, writes the Alpaca rows before calling the function a second time.

Given an order accepted at game time `T`:

1. From the bars for that ticker, take the first row with `t > T` and `t <= game_clock`. The left side is strict, so the bar that contains `T` cannot fill the order. A bar after the game clock is not visible. Minutes with no row are skipped. The next bar that exists is the fill bar.
2. If no row matches and the poller may be lagging, the repository fetches that ticker's window from Alpaca once, upserts into `league.bars` (`ON CONFLICT DO NOTHING`), and calls the fill function once more. The fill function itself does not perform this fetch.
3. Base price = that bar's open.
4. Slippage: buys pay `open × (1 + SLIPPAGE_BPS/10000)`, sells receive `open × (1 − SLIPPAGE_BPS/10000)`. Default `SLIPPAGE_BPS = 5`.
5. Commission is `COMMISSION_USD`, default 0. A buy changes cash by `cash -= qty * fill_price + COMMISSION_USD`. A sell changes cash by `cash += qty * fill_price - COMMISSION_USD`. The risk engine resizes a buy whose total would exceed available cash before the fill runs. One test uses a non-zero commission.
6. Quantity: for notional buys, `qty = floor_to_6dp(notional / fill_price)`; for sells, the requested qty (capped at the held qty).
7. If there is still no eligible bar after `FILL_TIMEOUT_MINUTES` (default 30 trading minutes; a halt or a long IEX gap), the order expires.

A unit test builds a bar series with a gap and asserts the fill uses the first bar that exists after `T`, not a synthesized bar inside the gap.

### 7.5 Orders accepted by the simulator

- Agent orders: notional USD (e.g. "buy $120 of AMD") or sell fraction of position (e.g. "sell 50% of AMD").
- Player orders: notional USD or quantity.
- Minimum order size: $5.

### 7.6 Corporate actions (v1)

- Splits: apply split adjustments to positions and `avg_cost` using Alpaca's corporate actions data (or a manual admin command if unavailable). Backtests use split-adjusted bars.
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
    config: AgentConfig              # interests, risk_level, style, exclusions
    portfolio: PortfolioSnapshot     # cash, positions with current prices, equity
    news: list[NewsItem]             # last 24h, filtered to interest subgraph, published_at <= game_time
    paths: list[GraphPath]           # candidate second-order paths from Neo4j
    proposals: list[OrderProposal] = []
    validation_errors: list[str] = []
    retries: int = 0
```

| Node | Type | What it does |
| --- | --- | --- |
| `gather_context` | Code | Loads config, portfolio, prices (Redis), news (Postgres/Kafka store) and candidate paths (Cypher). Caps: 20 news items, 15 paths. |
| `analyze_second_order` | LLM | Given news + paths, writes a short analysis of which companies are likely helped/hurt and why. |
| `propose_orders` | LLM (structured output) | Returns JSON only, matching `OrderProposal[]` (below). May return an empty list ("hold"). |
| `validate_rationale` | Code | Checks every proposal (rules below). On failure, sends errors back to `propose_orders` once (`MAX_RETRIES = 1`). |
| `risk_check` | Code | Applies section 9 limits; resizes or drops orders. The LLM never decides final size. |
| `submit_orders` | Code | Writes `rationales` + `orders`, publishes `orders.proposed` for both the actual and ghost portfolio. |

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
3. The `graph_path` is non-empty, ≤ 3 hops, ends at `ticker`, and every edge exists in Neo4j with `valid_from <= game_time` (checked with one Cypher query per path). This rule, and the news-id rule above, apply only when `system` is false. A system stop-loss rationale may have an empty path and empty `news_ids` (section 6).
4. At least one cited news item mentions a company on the path. Same exception: not required when `system` is true.
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

- Use a fast, cheap model for `analyze_second_order` and `propose_orders`; a stronger model only for weekly recaps. Model names live in config (`LLM_FAST_MODEL`, `LLM_STRONG_MODEL`).
- Temperature 0.2. Structured output via the provider's JSON/tool-calling mode.
- Per-agent daily token budget (`AGENT_DAILY_TOKEN_BUDGET`, default 60k). When exceeded, the agent holds until the next day.
- Log tokens and cost per cycle (`agent_cycles`).
- Cache identical prompts (hash of inputs) in Redis for 10 min — useful for agents with the same config reacting to the same event.

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

Processing order for each proposal: exclusions → interest subgraph → sells first → buys sorted by confidence desc → clip to max order size → clip to max position size → clip to available cash minus buffer → drop if < $5.

Stop-loss: `check_stop_losses(positions, prices, risk_level)` is a pure function. It returns sell orders whose rationales have `system=true`, `validated=true`, summary `"Stop-loss at -X%"` for that level's threshold, and empty `graph_path` and `news_ids`. The simulator calls it every 5 minutes on the game clock. That schedule is not part of the pure function. The repository inserts the rationale. Agent and LLM code cannot set `system`. The UI labels the trade "Auto stop-loss".

The same engine validates player orders (cash and no-shorting rules only; position limits don't apply to players).

## 10. Scoring, leaderboards and gamification

### 10.1 Metrics (per portfolio)

- Equity = cash + Σ(qty × latest price).
- Return = equity / starting_cash − 1.
- Excess return vs. SPY = return − SPY return over the same window.
- Sharpe (season) = mean(daily returns) / std(daily returns) × √252, risk-free = 0; shown only after ≥ 5 trading days.
- Max drawdown = max over t of (peak equity up to t − equity_t) / peak.
- Win rate = % of closed round-trips with positive P&L.

### 10.2 Leaderboard

Redis sorted set `lb:{season_id}` with score = return (higher is better); tie-break by Sharpe, then earlier join.

Recomputed every 5 minutes during market hours and at close; pushed to clients via WebSocket. Daily close snapshot stored in Postgres for history.

Separate tabs: Overall, Beat SPY, Best Sharpe, Agent-only (ghost).

### 10.3 Achievements (v1 list)

| ID | Name | Condition | XP |
| --- | --- | --- | --- |
| `first_trade` | Opening Bell | Agent's first filled trade | 50 |
| `diversified` | Spread the Love | Hold 5+ sectors at once | 100 |
| `beat_spy_week` | Index Slayer | Beat SPY over a calendar week | 150 |
| `called_it` | Called It | An agent buy gains ≥ 10% within 5 trading days | 200 |
| `diamond_hands` | Diamond Hands | Hold a position for 20 trading days | 150 |
| `overrule_win` | Trust Your Gut | Your overrides beat your ghost portfolio for a week | 200 |
| `podium` | Podium | Finish a season top 3 in a league with ≥ 5 players | 500 |
| `champion` | Champion | Finish a season #1 in a league with ≥ 5 players | 1000 |

Level = `floor(sqrt(XP / 100)) + 1`.

### 10.4 Seasons

Default length 4 weeks, starting on a Monday at market open.

At season end: final snapshot, awards, recap; new season created automatically; portfolios reset to starting cash. Agent configs carry over.

### 10.5 Weekly recap

Generated every Friday after close by the strong model from structured data (never invents numbers: the model receives the computed metrics and must only reference them). Rendered as a page and as an OG image via `next/og`.

## 11. Backtesting

Input: agent config, start/end date (max 6 months), decision frequency (`daily` default to control cost).

Engine: the same decision graph, simulator and risk engine, with an injected `Clock` that replays historical time. Fills read `league.bars` (loaded by `scripts/backfill_bars.py` or the poller). The same `t > decision_time` and `t <= sim_time` rule applies.

No look-ahead rules:

- News filtered to `published_at <= sim_time`.
- Graph edges filtered to `valid_from <= sim_time`.
- Fills use the first bar strictly after the decision time, and not after `sim_time`.

Output: equity curve vs. SPY, return, Sharpe, max drawdown, trades with rationales.

Limitation (show in UI): the LLM may have prior knowledge of past events from its training data, so backtests can be optimistic. Prefer windows after the model's knowledge cutoff when possible.

Backtests run as background jobs (Redis queue); results stored in Postgres (`backtests`, `backtest_trades` tables, same shape as live).

## 12. API

All endpoints under `/api/v1`. Auth is the existing QuantPilot bearer JWT (section 6.1), not Supabase. Errors: `{ "error": { "code": "STRING", "message": "..." } }`. These routes are not part of Week 1.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/me` | Current user, XP, level |
| POST | `/agents` | Create agent `{league_id, name, risk_level, style, interests[], excluded_tickers[], excluded_sectors[], mode}` |
| GET | `/agents/{id}` | Agent config + status |
| PATCH | `/agents/{id}` | Update allowed fields (section 3.3 rules) |
| GET | `/portfolios/{id}` | Cash, positions (with live prices), equity, metrics |
| GET | `/portfolios/{id}/equity?range=1d\|1w\|season` | Equity series + SPY series |
| GET | `/portfolios/{id}/orders?cursor=` | Paginated orders with fills |
| POST | `/portfolios/{id}/orders` | Player order `{ticker, side, notional_usd?, qty?}` |
| POST | `/orders/{id}/approve` · `/orders/{id}/reject` | Approve mode actions |
| GET | `/rationales/{id}` | Summary, news, graph path (nodes + edges for the 3D view) |
| GET · POST | `/leagues` · `/leagues/join` | List / create / join by invite code |
| GET | `/leagues/{id}/leaderboard?tab=` | Ranked list |
| GET | `/seasons/{id}/recap?week=` | Weekly recap data |
| GET | `/achievements` | All + unlocked |
| POST · GET | `/backtests` · `/backtests/{id}` | Start / poll backtest |
| GET | `/market/universe` · `/market/quote/{ticker}` | Universe list, latest price. The badge is "IEX prices" when `DATA_DELAY_MINUTES` is 0, and "Prices delayed N min" otherwise. |

WebSocket: `GET /ws?token=<JWT>`. Client subscribes with `{"op":"sub","channels":["portfolio:{id}","league:{id}","prices:{ticker}"]}`.

Server messages:

```json
{"type":"fill","portfolio_id":"...","order_id":"...","ticker":"AMD","side":"buy","qty":0.83,"price":144.21}
{"type":"equity","portfolio_id":"...","equity":1043.17,"t":"..."}
{"type":"leaderboard","league_id":"...","top":[{"user":"...","return":0.043,"rank":1}]}
{"type":"achievement","id":"called_it"}
{"type":"price","ticker":"AMD","c":144.35,"t":"..."}
```

Redis pub/sub channels with the same names feed the WebSocket hub.

## 13. Security, fairness and compliance

- Paper money only. No broker trading endpoints are ever called. Alpaca keys are paper-account keys used for data and the calendar only.
- Not investment advice: persistent footer + onboarding checkbox: "This is a game with simulated money. Nothing here is financial advice."
- Rate limits (Redis): player orders 30/min; API 120/min per user.
- Only the simulator writes portfolios, positions, and fills (single writer), and it does so through `backend/app/league/repo/`. The marketdata poller is the writer for `league.bars`, also through that repository. Fill and risk functions do not hold a session.
- Validate every input with Pydantic/Zod; tickers must match `^[A-Z.]{1,6}$` and be in the universe.
- Store no sensitive personal data beyond email/display name.
- Respect data-provider terms: do not redistribute raw market data feeds; show derived values in-app only.
- Anti-cheat: risk level locked per season; config edits rate-limited; all orders timestamped on the game clock server-side.

## 14. Repository structure

The target layout. Existing QuantPilot packages stay where they are. Week 1 does not rename `frontend/` to `web/` or replace `backend/app/market` (that package is the event-study engine).

```
quantpilot/
├─ backend/
│  ├─ app/
│  │  ├─ api/            # FastAPI routers (v1), websocket hub
│  │  ├─ core/           # existing pydantic-settings; League settings are a new module
│  │  ├─ db/             # existing models and session; League uses its own MetaData
│  │  ├─ sim/            # clock.py, fills.py, risk.py, portfolio.py, stoploss.py
│  │  ├─ agent/          # graph.py (LangGraph), nodes/, prompts/, schemas.py, validate.py
│  │  ├─ graph/          # neo4j client, cypher queries, path validation
│  │  ├─ market/         # existing event-study engine; League Alpaca code does not go here
│  │  ├─ league/         # repo/, alpaca data client, poller, calendar, later leaderboard
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
│  │  ├─ three/          # MarketGalaxy, GraphPath3D, Trophy, AgentAvatar
│  │  ├─ charts/         # EquityChart (lightweight-charts), AllocationDonut (recharts)
│  │  ├─ effects/        # Confetti, Particles, NumberTicker (Magic UI/Aceternity copies)
│  │  └─ ui/             # shadcn components
│  ├─ lib/               # api client, ws client, zod schemas; existing auth, no Supabase
│  └─ public/models/     # .glb files + LICENSES.md
├─ config/universe.csv          # written by scripts/build_universe.py
├─ scripts/backfill_bars.py
├─ scripts/build_universe.py
├─ scripts/check_iex_latency.py
├─ docker-compose.yml
└─ docs/QUANTPILOT_LEAGUE.md    # this spec. Root DESIGN.md stays the UI system.
```

Where a target path already exists and means something else (`backend/app/market`, `backend/app/graph`, `backend/app/db/models.py`, `frontend/`), new League modules do not overwrite those files. Alpaca code for League goes in a new module under `backend/app/league/` (data client, poller, calendar). It does not replace the event-study package `backend/app/market`. Neo4j path checks, when they are built, extend the existing `backend/app/graph` client instead of adding a second driver. That extension is not Week 1.

## 15. Configuration (environment variables)

```
# Data
ALPACA_API_KEY=            ALPACA_API_SECRET=       ALPACA_DATA_FEED=iex
DATA_DELAY_MINUTES=0
UNIVERSE_PATH=config/universe.csv
# Infra
DATABASE_URL=postgresql://...              REDIS_URL=redis://...
KAFKA_BOOTSTRAP=localhost:9092             NEO4J_URI=  NEO4J_USER=  NEO4J_PASSWORD=
# LLM
LLM_PROVIDER=anthropic|openai   LLM_FAST_MODEL=   LLM_STRONG_MODEL=   LLM_API_KEY=
AGENT_DAILY_TOKEN_BUDGET=60000
# Game
STARTING_CASH=1000   SEASON_WEEKS=4   SLIPPAGE_BPS=5   COMMISSION_USD=0
FILL_TIMEOUT_MINUTES=30   EVENT_IMPACT_THRESHOLD=0.7   MAX_EVENT_CYCLES_PER_DAY=3
MIN_ORDER_USD=5
```

`DATA_DELAY_MINUTES=0` is the default because `ALPACA_DATA_FEED` defaults to `iex`. Set it to 15 only for SIP without a subscription (section 7.2). `DATABASE_URL` is the existing sync URL. There are no Supabase settings. `UNIVERSE_PATH` is the loader input (section 7.1).

## 16. Testing strategy

| Area | Tests |
| --- | --- |
| Fill engine | Fills use the first existing `league.bars` row with `t > order_time` and `t <= game_clock`; a gap test (no synthesized bar inside a hole); slippage direction; fractional qty rounding; expiry on halt; one Alpaca fallback fetch then expiry |
| Risk engine | Each limit in section 9 table, per risk level; processing order. Agent and LLM paths cannot set `rationales.system`. Only the stop-loss result may, and that row has empty `graph_path` and `news_ids` with `validated = true`. |
| Property tests (hypothesis) | For random order sequences: cash never < 0, qty never < 0, equity = cash + Σ positions, ghost and actual diverge only via player orders |
| No look-ahead | Backtest on synthetic data with a known future spike: agent must not "see" it |
| Rationale validation | Fake news IDs, future-dated news, non-existent edges, wrong path end → all rejected |
| Agent graph | LLM mocked with fixed JSON; retry path; empty proposals |
| API | Auth required; permission checks (can't read others' pending orders); rate limits |
| Frontend | Component tests for leaderboard reordering and approve-mode flow; Playwright smoke test of onboarding → first trade visible |

## 17. Observability

Structured logs (`structlog`) with `cycle_id`, `agent_id`, `order_id`.

Admin page `/admin` (role-gated): cycles per hour, validation failure rate, LLM cost per day, fills per hour, WebSocket connections.

Alerts (simple): marketdata stale > 5 min during market hours; simulator lag > 2 min.

## 18. Build plan (5 weeks)

| Week | Build | Done when |
| --- | --- | --- |
| 1 | Simulator core: `league` schema and its own Alembic version table, universe fixture plus `scripts/build_universe.py`, game clock, Alpaca bars poller writing `league.bars`, calendar, pure fill and risk functions, repository, property tests, `scripts/backfill_bars.py`, `scripts/check_iex_latency.py`, and a committed one-day bar fixture loaded into `league.bars` for the replay | A script places random orders for 1 day of replayed bars and all invariants hold. `public` is unchanged. The replay bars come from `backend/tests/fixtures/bars_one_day/`, produced by the backfill script, not invented. |
| 2 | Agent: impact events, interest subgraph, LangGraph cycle, rationale validation, scheduler | Agents trade on live data; every trade has a validated rationale |
| 3 | API + auth + WebSocket; Next.js onboarding, dashboard (equity chart, positions, trade feed) | A new user can sign up and watch their agent trade |
| 4 | Leagues, seasons, leaderboard, achievements, approve mode, you-vs-agent, trade detail 3D graph, landing galaxy | 5 friends in a private league with a live leaderboard |
| 5 | Backtest lab, weekly recap + OG image, polish, deploy, launch to Waterloo clubs, demo video | Public URL live, first 25+ users |

## 19. Out of scope for v1

Real-money trading or broker integration · options, crypto, shorting, margin · limit/stop orders for players · dividends · chat between players · mobile native apps · non-US markets · user-written custom strategies/code · pruning or partitioning of `league.bars`.

## 20. Metrics to report (resume / demo)

- Users, leagues, retention (weekly active players)
- Agent cycles run, trades executed, % of LLM proposals rejected by rationale validation (shows the guardrail works)
- Backtest results of each style vs. SPY (report honestly, including underperformance)
- p95 latency: event published → agent trade filled
- LLM cost per active agent per day

## 21. Disclaimers

QuantPilot League is a game using simulated money. It does not provide investment advice. Past or simulated performance does not predict future results.
