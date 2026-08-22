# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The builder of this repo — a technical operator who wants to see a multi-agent equity bench run, inspect state, and poke the graph. Not a startup customer, not a retail-app audience. Copy and chrome should assume the reader knows what LangGraph, a runner, and a verification pass are.

## Product Purpose

QuantPilot is a personal multi-agent bench for equities. The job is still "why did this move?" — ranked claims in an Evidence Ledger, plus an optional full research graph — but the point of the project is the orchestration: specialized agents, shared state, and an adversarial pass that can demote the lead. Success is a correctly-sourced case you can step through, not a polished consumer app.

## Positioning

Not a black-box AI summary or a single-prompt paraphrase of a headline. The mechanism a competitor can't truthfully copy without the same architecture: a multi-agent pipeline (news, financial, SEC, earnings, macro, risk, bull/bear, verification, report, memo) that keeps grounded facts (prices, filings, headlines) separate from model insight, ranks hypotheses into an Evidence Ledger, verifies claims against collected evidence, and runs a Devil's Advocate pass that can demote its own leading claim. The product is willing to show its work and challenge itself — most AI research tools just assert a conclusion.

## Operating Context

Web dashboard (Next.js on Vercel) talking to a FastAPI backend (Railway). Core workflows:

- **Reactive investigate** — enter a ticker and a window (day/week/month/6 months/year) → move detection → Evidence Ledger of ranked, cited claims.
- **One-click deep research** — full multi-agent report plus a shareable Investment Memo.
- **Portfolio OS** — watchlist, holdings, portfolio auto-sync (via pasted positions or an existing signed-in browser session snapshot — never credentials), daily briefings, smart alerts.
- **Ledger search** — natural-language search across past investigations, claims, and evidence.

Auth is JWT-based so history and a pasted portfolio can persist on this bench. There is no public launch, no GTM, and no customer story.

## Capabilities and Constraints

- Market data via yfinance; filings via SEC EDGAR; optional IR/browser evidence via OpenClaw with an allowlisted httpx fallback; OpenAI (GPT-4o-mini) for generation and embeddings.
- No real brokerage API integration exists or is planned as a near-term fact — portfolio positions come from a pasted positions page or an existing-session browser snapshot. Credentials never pass through QuantPilot; this is a hard constraint, not an implementation detail to soften.
- PostgreSQL (SQLite locally) for users/portfolios/Evidence Ledger; ChromaDB for vector report and investigation search; Redis is optional with an in-memory fallback for caching and job queues.
- Standing terminology: Evidence Ledger, claims, hypotheses, Investigation, Devil's Advocate, Investment Memo, LangGraph, investigation runner.

## Brand Commitments

Product name is "QuantPilot." It is a personal project, not a company.

Visual world (2026-08-22): user-pinned Cyberpunk / Glitch design system. Void #0a0a0f, card #12121a, acid green #00ff88, magenta #ff00ff, cyan #00d4ff, destructive #ff3366. Orbitron headings, JetBrains Mono body, Share Tech Mono labels. Chamfered clip-path panels, neon glow, scanlines, chromatic glitch. Motion stack is GSAP, Lenis, and a Vanta net clipped to the quote HUD.

## Evidence on Hand

No customers, testimonials, or usage stats — this is a solo bench. Do not invent launch, pricing, or adoption claims. A dashboard screenshot exists at `./assets/dashboard.png` for internal reference.

## Product Principles

1. Every claim must trace to real, cited evidence — never present unsourced model opinion as settled fact.
2. Show the pipeline's work, not just its conclusion; transparency is part of the value, not overhead to hide.
3. Adversarial self-check (verification, Devil's Advocate) is a feature — the product should visibly be willing to demote its own top hypothesis.
4. No real user financial credentials ever pass through the product; portfolio data enters only via paste or session snapshot.
5. Design for the person who built the graph — show orchestration, node names, and failure modes. Do not hide the pipeline behind consumer copy.
