# QuantPilot project audit

Audited 2026-10-05 against commit `55a91b71d88105a9fb3288b8bd70db5ee09a89e7`. No code was changed for this review.

This is a hiring-manager reading of the repository, the phase notes, and the staging measurements. It is not a product plan and it does not treat the architecture diagram as evidence.

## What was inspected

- `README.md`, `PRODUCT.md`, `ARCHITECTURE.md`, `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE_DECISIONS.md`
- `docs/phases/phase9-results.md`, `docs/phases/phase10-results.md`, `docs/PHASE4_8_PLAN.md`, `docs/MARKET_IMPACT.md`, `docs/GRAPH_SCHEMA.md`, `docs/RELATIONSHIP_CANDIDATES.md`, `docs/RETRIEVAL.md`, `docs/RETRIEVAL_EVALUATION.md`, `docs/LANGGRAPH_WORKFLOW.md`
- `backend/app/research/graph.py`, `plan.py`, `verify.py`, `llm.py`
- `backend/app/retrieval/search.py`
- `backend/app/market` and `docs/MARKET_IMPACT.md`
- `backend/app/graph/candidates.py`, `projector.py`
- `backend/app/pipeline/kafka/topics.py`, `backend/app/pipeline/spark/`
- `infra/terraform/` (`database.tf`, `kafka.tf`)
- Frontend routes under `frontend/app/`
- Git history from the investigation-desk era through Phase 10.1

Measured size at this commit: `backend/app` is 267 Python files and about 46,300 lines. Tests are 128 files and about 20,500 lines, 106 of them named `test_*.py`. The frontend is about 9,100 lines of TypeScript. Spark is about 1,800 lines. The research package is about 7,500. The graph package is about 2,600. Alembic has 14 versions.

`docs/CURRENT_STATE.md` and `ARCHITECTURE.md` describe an older system (Chroma, yfinance, Vercel, Railway, synchronous investigations). They are not the system that was deployed. `README.md` and the phase-result notes are the accurate surface. A recruiter who opens `ARCHITECTURE.md` first will think the project still recommends trades from a portfolio sidebar.

---

## 1. Executive verdict

**Overall: 7/10** as a flagship undergraduate systems project. That score is for software that was built, deployed, and measured. It is not a score for quantitative finance, and it is not a score for production scale.

| Dimension | Score | Why |
| --- | --- | --- |
| Software engineering | 8/10 | Typed contracts, Alembic, idempotent writes, leases, an outbox, and tests that refuse to coerce a bad planner string into a list. The Phase 9 planner failure was reproduced, fixed, deployed, and remeasured. The cost is a second, older product still living in the same tree. |
| Data engineering | 7/10 | Massive news and daily bars actually moved Massive → Kafka → S3 → Spark → Postgres on AWS, with schemas, reject objects, and a manifest. The corpus is 72 articles and 264 bars. Spark here validates and partitions small files. It does not demonstrate a hard data problem. |
| Distributed systems | 6.5/10 | At-least-once research delivery, idempotent consume, leases, DLQ topics, and partition specs are real (`backend/app/pipeline/kafka/topics.py`, `backend/app/research/outbox.py`). Staging Kafka is one EC2 node, replication factor 1, one worker. The formal load matrix in `docs/PHASE4_8_PLAN.md` used FakeLLM and fixture documents. |
| AI engineering | 7.5/10 | The research graph is a bounded evidence package, specialist nodes, a verifier, and citation IDs (`backend/app/research/graph.py`, `verify.py`). Staging showed the planner schema bug and the fix. Retrieval is brute-force cosine over every stored vector (`backend/app/retrieval/search.py`). Verification is lexical and regex, not entailment. |
| FinTech relevance | 6/10 | Real provider, real URLs, event windows versus a configured benchmark, and an explicit refusal to emit buy/sell/hold. A Microsoft question returned NVDA windows. That is a financial-data correctness miss, and it caps the score. |
| Quantitative finance | 4/10 | `docs/MARKET_IMPACT.md` defines simple session returns, relative return as asset minus SPY, a volume ratio, and a sample standard deviation. It says this is not CAPM and not causal. That honesty is correct. It is not an event study, a factor model, or a test of a hypothesis. |
| Investment banking relevance | 4/10 | Useful as a story about research technology and source control. Weak for analyst work. There are no comps, no three-statement model, no accretion/dilution, no CIM-style writeup, and no parsed 10-K financials in the staging path. Phase 7 loaded SEC company listings, not filings. |
| Trading / research relevance | 4/10 | The research-desk workflow is closer to a junior research associate's source check than to a trading system. There is no order, book, fill, or risk limit. |
| Data science | 4/10 | Embeddings exist (`text-embedding-3-small`, 1,536 dimensions, 72 vectors). The retrieval eval in `docs/RETRIEVAL_EVALUATION.md` uses fake lexical hashes on 24 synthetic documents and tells the reader not to publish the scores. There is no held-out precision number on the real corpus. |
| Portfolio / recruiting value | 7.5/10 | For software, data, and AI co-op screens this is already a flagship if the bullets stay inside the measured facts. It becomes average the moment the resume says "quantitative trading platform" or "knowledge graph of the market." |
| Differentiation | 7/10 | The differentiated part is evidence lineage plus a public failure: 12/12 malformed plans, then 0/15 after a schema constraint, with the keyword router left in place. Most student LLM projects hide that. The graph, Spark cluster shape, and agent count are not the differentiator. |
| Production engineering maturity | 5/10 | Staging is real: ECS, RDS, ALB, secrets, `/health` and `/ready`, correlation IDs. RDS is `multi_az = false` (`infra/terraform/database.tf`). Kafka is one instance. The ALB is HTTP. One worker. This is a careful staging environment, not a production system. |

Do not read 7/10 as "almost a startup." Read it as "a strong co-op project with one honest production-shaped slice and several architectural promises that the data does not yet support."

---

## 2. How impressive is this really?

Compared with a Waterloo undergraduate applying to co-op:

| Bar | Placement | Why |
| --- | --- | --- |
| A. Typical undergraduate projects | Exceptional | Most are a CRUD app or a chatbot over a PDF. This has migrations, a lake, a worker, staging, and a written record of a model failure. |
| B. Strong Waterloo CS/Math projects | Strong | The better course and design projects have similar test discipline. QuantPilot is ahead on deployment and behind on a single deep algorithm or a clean, small codebase. 46,000 lines for 72 articles is a lot of surface area. |
| C. Strong co-op candidates | Strong | A hiring manager will stop for the staging matrix and the citation rule. They will not stop for the technology list. Candidates from real fintech co-ops will have smaller systems that touched production data volumes this project has not. |
| D. Serious open-source projects | Below average | There is no external user, no release, and no issue history from anyone else. The docs are better than many OSS repos. The product is not. |
| E. Early-stage startup engineering | Average | The outbox, lease, and schema-validation habits look like early startup code. The workload does not. A startup engineer is judged on operating the system, not on having drawn Kafka. |
| F. Junior FinTech engineering projects | Strong | Junior fintech portfolios are often a dashboard on Yahoo Finance. This one refuses to invent a relationship and records URL coverage. It is weak next to an intern who shipped a recon job or a market-data normalizer at a firm. |

What makes a recruiter stop:

- Phase 9: 12/12 research jobs succeeded and 12/12 planner outputs were malformed, then Phase 10.1: 15/15 valid plans, 0 fallback, on the same corpus.
- 113/113 document and event citations carried HTTPS URLs, and market citations were kept out of that denominator.
- The rule, implemented in `backend/app/graph/candidates.py`, that co-mention is not an edge. Staging still has 0 relationships. That is the correct outcome, and it is rare.

What does not make them stop: Neo4j, "multi-agent," PySpark, or pgvector, stated as nouns.

---

## 3. FinTech relevance

| Role family | Score | Already demonstrated | Missing | What would make it stronger |
| --- | --- | --- | --- | --- |
| FinTech, general | 6/10 | A research object with sources, status, and limitations, deployed behind an ALB | A user, a permission model that matters, and a workflow someone repeats | Fix the symbol join, then show one query a markets person would rerun |
| Financial data engineering | 7/10 | Vendor adapters, raw lake, schema rejects, canonical bars, idempotent reload | Corporate actions, adjusted prices actually used, symbology, late bars | Use `adj_close` or document why unadjusted is the contract, and reject a bad vendor print in a recorded run |
| Financial software engineering | 7/10 | FastAPI, typed reports, frontend that shows verification | The old investigation desk still routed (`frontend/app/investigate`, `dashboard`) | One product surface |
| Market data engineering | 5/10 | Daily bars for six symbols, 44 sessions each | Intraday, calendars beyond session offsets, corporate actions, snapshot vs official close | A trading calendar test and an explicit missing-bar policy are already sketched in `docs/TRADING_CALENDAR.md`. They are not a market-data platform |
| Financial AI | 7/10 | Grounded generation, citation IDs, unsupported claims excluded from factual sections | Entailment checking, retrieval metrics on real text | A labeled set of 30 claims scored by a human against the article |
| Quantitative research engineering | 4/10 | Window arithmetic with a benchmark and a versioned calculation (`calculation_version = v1`) | Estimation window, beta, significance, point-in-time universe | One market-model abnormal return, with the sample size printed |
| Investment research technology | 6/10 | Desk that shows FACT / OBSERVATION / INFERENCE, limitations, agent attempts | Coverage universe, analyst workflow, filing text | Full article bodies, not headlines and descriptions |
| Risk technology | 3/10 | A volatility number on an event window | Limits, exposures, scenarios that are risk scenarios, P&L explain | Do not pretend the current volatility field is a risk system |

Using financial data is not quantitative finance. The project already says so in `docs/MARKET_IMPACT.md`. Keep saying so.

---

## 4. Investment banking relevance

| Role | Does this help? |
| --- | --- |
| IB analyst | Barely. Analysts are hired for accounting, models, and process. This repo does not show a merger model, a comps set, or a memo a VP would edit. |
| IB technology | Some. Source lineage, auditability, and "the model said X and we did not ship it" are the right instincts for a bank technology interview. |
| Capital markets technology | Some, on the data path only. No market-structure, no symbology, no entitlements. |
| Equity research technology | Meaningfully, if the candidate can walk the citation path. This is the closest IB-adjacent fit. |
| Equity research analyst | Barely. The reports restate articles. They do not build a variant view, a forecast, or a debate. |
| M&A technology | Barely. No deal objects, no ownership chains, no filings. |
| Risk technology | Barely. |
| Markets / trading technology | Weak. Daily bars and a research worker are not a trading stack. |

Recruiting consequence:

- FinTech engineering and equity-research technology: the project helps.
- Quant, markets, and IB analyst: the project helps only as proof you can build software. It does not substitute for a model, a paper, or a trading simulation.
- Do not put this under "Investment Banking" on a resume. Put it under projects, and name the engineering.

---

## 5. Quant / trading relevance

Four different jobs get conflated. QuantPilot sits in one of them.

| Label | What it actually requires | Where this project sits |
| --- | --- | --- |
| Financial AI engineering | Ground model output in records, evaluate it, deploy the job | This is the fit |
| Investment research technology | A repeatable desk an analyst can audit | Partial fit. The desk exists. The corpus is a demo |
| Quantitative finance | A hypothesis, a return definition, a baseline, a test | Not this project. Relative return versus SPY is a descriptive residual, and the doc says so |
| Trading systems | Orders, positions, latency, controls | Not present, and should not be added to decorate the resume |

ROI of common "quant" additions, highest first. Do not build the rest.

| Rank | Addition | Why it is or is not worth it |
| --- | --- | --- |
| 1 | Market-model abnormal return on the existing windows | One estimator, one t-stat or an explicit "window too short," reuses `event_impacts`. This is the difference between a calculator and a study |
| 2 | Beta of each name versus SPY over a stated window, with the sample size | Small, legible, and it makes the benchmark choice inspectable |
| 3 | Point-in-time note: unadjusted close, no survivorship story because the universe is five names you chose | Honesty, not a feature |
| 4 | Earnings-surprise join | High value later, only after a filings or estimates source exists. There is no estimates feed now |
| 5 | A single backtest of "do not trade" | Low value. There is no signal |
| — | Fama-French, portfolio optimization, options IV, order book, execution sim, forecasting | Negative ROI on this corpus. Five names and 44 sessions cannot identify a factor. An order-book simulator would be a different project |

Sentiment analysis would mostly rediscover the article headline. Skip it.

---

## 6. Data engineering assessment

Genuinely strong:

- One ingestion contract, three adapters, raw objects kept verbatim, processed Parquet with explicit schemas and reject rows (`backend/app/pipeline/spark/transformers/news.py`, `jobs/runner.py`).
- Idempotent canonical load. Phase 9 records 341 loaded ingestion records and a failed market run that was not replayed after the volume type change (migration `0013`).
- Lineage from provider object to document, event, and impact row.
- The global `--max-records` cap bug was found and written down: one run filled the cap with AAPL. That is data-engineering judgment.

Unnecessary for the workload, but not fake:

- PySpark for 336 small records. The code is a real Spark job, run as a one-off ECS task (`phase9-spark`), not a standing cluster. Keeping it is justified only as a batch contract you intend to grow. It is not justified as evidence of big-data skill.
- Kafka in front of a few hundred messages. The valuable part is the outbox and the DLQ, not the broker.

Missing relative to a strong data-engineering co-op portfolio:

- A data-quality table someone can query: late bars, duplicate vendor IDs, schema drift over time.
- A scheduler with run history. Airflow is not in the repo. Do not add it for the noun. A single idempotent job runner with a visible run table is enough.
- Warehouse modeling beyond the operational Postgres schema. There is no dimensional model because there is not enough data to need one.
- A backfill story that is boring and documented. Phase 9 is close.

Against a strong DE co-op candidate: you are ahead on correctness culture and behind on volume, orchestration, and operating a pipeline that other people break.

---

## 7. Distributed systems assessment

Strong decisions:

- Transactional outbox for research requests, then a worker. The API does not run the graph inside the POST.
- Lease and reclaim on research jobs (`renew_lease` in the research worker path). A dead worker does not own the job forever.
- Topic specs that fail on partition or replication drift (`backend/app/pipeline/kafka/topics.py`). News and market are specified at 3 partitions. The DLQ is 1. Staging replication factor is 1, so `min.insync.replicas` collapses to 1. That is an honest single-node design, not a quorum.
- Idempotency keys on `POST /api/research`. Phase 4.8 measured duplicate submits against FakeLLM and recorded 0 duplicate jobs. That measurement is local, not staging.

Over-engineered relative to the workload:

- A research completion topic plus a request topic plus a research DLQ, with one consumer. The completion notice is not what the UI reads. The UI polls Postgres.
- Spark advisory locks and a JVM for a batch that a Python validator could finish. Defensible as a contract. Easy to over-claim.

What the system does show:

- At-least-once delivery: yes, and the consumer is written to be idempotent.
- Eventual consistency: yes, between outbox, Kafka, and the job row. Phase 10.1 jobs were observed moving `publish_pending` → `queued` → `running` → `succeeded`.
- Failure recovery: leases and DLQ exist. The staging matrix had 0 failures, so recovery was not what that matrix measured.
- Backpressure: `RESEARCH_MAX_QUEUED_JOBS` exists. It was not the binding constraint at 15 jobs.
- Partitioning: specified, not used. One worker.
- Concurrency: Phase 4.8 ran multiple FakeLLM workers locally. Staging runs one.
- Distributed coordination: a Postgres lease and a Spark advisory lock. No consensus protocol, and none is needed.

---

## 8. AI engineering assessment

This is not a generic LLM wrapper. A wrapper would send the user string to the model and print the reply.

It is a RAG application only in the narrow sense that chunks are embedded. With 72 chunks, one per article, and cosine computed in Python over the full set (`search.py` loads every matching embedding and calls `cosine`), pgvector is a column type, not a retrieval engine. The comment in `search.py` says "hybrid." The ranker is vector-only after SQL filters. There is no BM25.

It is a genuine AI engineering system because the failure mode is designed:

- The planner must return a closed schema (`master_response_format` in `backend/app/research/llm.py`).
- A bad plan falls back to a keyword router instead of running an invented agent.
- `verify.py` marks missing citations, unknown citations, investment language, causal market wording, and unsupported relationships as `UNSUPPORTED`.
- Inference and scenario claims stay `UNCERTAIN` even when cited.
- Staging measured the before and after.

It is not yet a strong agentic system. The specialists do not choose tools. They read one evidence package and emit findings. The "agent runs" list includes skipped nodes. That is orchestration, which is the right scope. Calling it autonomous research would be false.

Strongest AI component: the planner constraint plus the verifier's refusal rules, because both were tested against a real model and the failure was kept in the docs.

Weakest AI component: retrieval and grounding of market rows. The embedding index is not used as an index, the text is a headline and description, and a Microsoft question was answered with NVDA impact rows. The verifier cannot catch a wrong ticker if the observation says "the stock."

The corrective retry did not fire on the 15 staging jobs (attempt 1 on every master run). The unit tests cover it. Staging does not.

---

## 9. Knowledge graph

The graph is architectural potential plus a careful negative result.

Postgres is the source of truth. Neo4j is an optional projection (`docs/GRAPH_SCHEMA.md`, `backend/app/graph/store.py`). Staging readiness reports Neo4j unconfigured. Relationship candidates require an explicit cue and an evidence row (`backend/app/graph/candidates.py`). Co-mention is rejected. Fixtures can seed edges. The real corpus has 0 candidates and 0 relationships (`docs/phases/phase9-results.md`).

Phase 11 should not start with Neo4j. Projecting zero edges into a second database does not make them true. Populate Postgres `relationships` from sentences that already say the thing, or keep the count at zero.

Top relationship types, given this product:

| Rank | Type | Why |
| --- | --- | --- |
| 1 | `PARTNERED_WITH` | The questions people ask, and the cue list already exists. Still easy to over-fire on the word "partner" |
| 2 | `ACQUIRED` | High value, rarer, easier to audit |
| 3 | `SUPPLIES` | Connects the technology story to a company without pretending a co-mention is `USES` |
| 4 | `COMPETES_WITH` | Useful and dangerous. Only when the sentence says competition |
| 5 | `CUSTOMER_OF` | Same rule |

`USES` and `DEVELOPS` are lower priority. The research prompts already say a technology mention is not `USES`. Turning headlines into `USES` edges would undo the best graph decision in the repo.

How to populate without hallucinating:

- Run the existing cue check over the 72 summaries.
- Accept only when both endpoints resolve by ticker or CIK, the cue is in the cited span, and a human or a deterministic rule marks `OBSERVED`.
- Leave `INFERRED` unused until there is a second source.
- Publish the reject count next to the accept count. If the result is still zero, write that down again.

---

## 10. Financial modeling gap

Current analytics are enough for basic descriptive windows. They are not enough for an equity-research conclusion or a quant study. `return` is `close(T+N) / close(T0) - 1`. Relative return subtracts SPY. Volume ratio is event-day volume over the prior 20 sessions. Volatility is a sample standard deviation. Prices are unadjusted. Missing bars stay null.

Top 3 improvements:

| Rank | Change | Engineering | Financial value | Recruiting value |
| --- | --- | --- | --- | --- |
| 1 | Bind impact rows to the entity the question resolved, and name the ticker in the observation | Medium. The bug is a join, not a new model | High. A wrong symbol makes every return false | High for every fintech screen |
| 2 | Market-model abnormal return: estimate alpha and beta on a pre-event window, report abnormal return and the number of sessions used | Medium | High relative to everything else you could add | High for quant-developer screens, modest for pure software |
| 3 | Use adjusted closes, or print "unadjusted" on every market card | Easy | Medium. Splits will silently distort the current series | Medium |

Do not add a DCF.

---

## 11. Dataset / corpus quality

72 headlines and 264 daily bars are appropriate for a portfolio demo and for catching pipeline bugs. They are sufficient to evaluate planner schema compliance. They are insufficient for quantitative research. Ticker tags are skewed: Phase 9 records AAPL on 50 articles because a global cap filled on the first symbol.

| Size | Verdict |
| --- | --- |
| 72, current | Right size for the demo you have. Do not apologize for it. Do not run a factor regression on it |
| ~100 | Not a meaningful step |
| 300–500 full-text articles, same six symbols, with a hand-labeled subset of 40 | The next useful size. Enough to measure retrieval and to find a handful of real partnership sentences |
| 1,000 | Worth it only if labels exist for a sample. Unlabeled volume does not improve the audit |
| 10,000+ | Diminishing returns until the join, the chunker, and the evaluator are trusted. You would be operating a collector, not improving the research |

The point of diminishing returns is the first corpus on which a stranger can compute precision of citations and precision of relationships, and both numbers move. That is hundreds of documents, not tens of thousands.

---

## 12. Evaluation quality

Meaningful:

- Staging job status, counted, with IDs in `docs/phases/phase10-results.md`.
- Planner valid / malformed / fallback / attempt, compared only to the Phase 9 deployed baseline.
- Citation URL coverage with market rows excluded from the denominator.
- The unit test that a string `evidence_requirements` stays invalid.

Weak:

- "Supported" means the verifier's rules passed, not that a human agreed. A supported fact can still be a bad paraphrase of a headline.
- Required-domain hit rate uses labels that are not the production router. 14/15 is a reading of those labels. It is not accuracy against a market.
- Local planner eval (24/24) used an empty evidence package. The docs already refuse to merge it with staging. Good.
- Retrieval metrics are on fake embeddings (`docs/RETRIEVAL_EVALUATION.md`).
- Phase 4.8 latency is FakeLLM. It must not appear next to the staging research numbers.

Gameable:

- Citation URL coverage, if someone cites the same HTTPS article 100 times.
- Claim counts. More agents produce more claims. 264 claims are not better than 110.
- Agent-run success. Skipped agents are successful skips.
- Test count. 113 research/retrieval tests passing does not exercise the Massive corpus.

Five metrics worth keeping:

1. Planner valid rate and fallback rate on staging, with the prompt version.
2. Human agreement on a fixed set of 30 claims: supported by the cited span, or not.
3. Retrieval Recall@5 on a labeled slice of the real corpus, using the real embedding model.
4. Wrong-symbol rate: share of market citations whose symbol is not the entity the question named.
5. Relationship precision: accepted edges that a reader confirms, over accepted edges. Publish the zero.

---

## 13. Product quality

A recruiter can tell what the homepage is in about 30 seconds. The first screen is a research box, and the subtitle says a market window is a measured return, not a cause (`frontend/app/page.tsx`, `ResearchDesk`). They cannot tell in 10 seconds, because the page does not show a finished report until they submit. At 2 minutes, if a job completes, they see stamps, links, and limitations. That is enough.

A user would not come back. The corpus is five companies and two months of headlines. The killer workflow is not "ask anything." It is: pick one article, show the event, show the sessions around it, and show which sentences were not used.

The homepage should open on a completed NVDA or MSFT report with the symbol printed on every market card, one HTTPS link already visible, and the unsupported claim visibly excluded. The demo query should be a question the corpus can answer, for example the NVIDIA news-and-window query that cited the $46 billion Motley Fool article. Do not demo "Tell me about the situation."

What would make it feel like research software: one object per event, the source sentence beside the claim, the ticker on the return, and a count of what was excluded. More agents would make it feel like a student project.

---

## 14. Architecture critique

Current path that actually runs in staging:

```
Massive news + daily bars
        │
        ▼
Ingest adapters  →  Kafka (1 broker, RF 1)  →  S3 raw
        │
        ▼
PySpark one-off  →  Parquet + rejects + manifest
        │
        ▼
Postgres canonical rows  →  documents, 1 chunk each, pgvector column
        │
        ▼
NEWS events + event_impacts (v1, vs SPY)
        │
        ▼
POST /api/research  →  outbox  →  Kafka  →  one worker
        │
        ▼
LangGraph: package → master-v4 plan → specialists → verify → report
        │
        ▼
Next.js research desk polls GET /api/research/{id}

Neo4j: not configured
Relationships: 0
Airflow: not in the repo
Redis: memory fallback on the ready check
```

Five strongest decisions:

1. Postgres is authoritative and Neo4j is derived.
2. Co-mention cannot become an edge.
3. Research is a durable job, not a request-scoped LLM call.
4. Market math is versioned and labeled non-causal.
5. Phase notes separate local eval from staging.

Five weakest:

1. Market evidence is not constrained to the company in the question.
2. Retrieval loads all vectors and scores them in Python.
3. Article text is a headline and description, so every downstream number is about that text.
4. `ARCHITECTURE.md` and `docs/CURRENT_STATE.md` describe a different product.
5. The old investigation stack (Chroma, yfinance, `frontend/app/investigate`) is still in the tree beside the desk.

Five over-engineered pieces:

1. Spark as a required mental model for this volume.
2. A graph database with no edges in the environment you demo.
3. Multiple Kafka topics for a single consumer that the UI does not read.
4. A specialist per domain when the package is 72 short documents.
5. Two research UIs.

Five missing pieces:

1. A correct entity-to-symbol join on impact rows.
2. A human-scored claim sample.
3. Full-text chunks.
4. One evidenced relationship, or a published extract that found none in the body text.
5. A README diagram that matches staging, not the target architecture.

| Component | Class | Why |
| --- | --- | --- |
| Kafka | KEEP | The outbox and DLQ are the lesson. Do not add brokers |
| S3 | KEEP | Raw vendor payloads are the right lake, even when small |
| PySpark | SIMPLIFY | Keep the job for the contract. Stop describing it as a data platform. Do not add a standing cluster |
| PostgreSQL | KEEP | It is the product |
| Parquet | KEEP | Processed output of the Spark job. Fine |
| pgvector | SIMPLIFY | Either query it with the database operator or stop listing it as search |
| LangGraph | KEEP | The node boundaries match the audit trail |
| Neo4j | DEFER | Until Postgres has edges worth projecting |
| Airflow | DEFER | Not present. A cron or one ECS scheduled task is enough |
| Redis | SIMPLIFY | Ready reports memory. Do not make it a dependency for the story |
| Docker / ECS / Terraform | KEEP | Staging is the proof. Do not expand the footprint |

---

## 15. Resume value

Strongest facts you can defend:

1. Deployed an evidence-backed research worker to AWS and measured it: Phase 9 had 12/12 malformed planner outputs; Phase 10.1 had 15/15 valid plans, 0 fallback, 15/15 jobs succeeded.
2. Ran a real vendor corpus through Kafka, S3, Spark, and Postgres: 72 news articles, 264 daily bars, 113/113 document and event citations with HTTPS URLs.
3. Built a verifier that drops unsupported claims and refuses to treat a co-mention as a relationship. The staging graph has 0 edges.

Metrics worth printing: those counts, the planner before/after, URL coverage, migration `0013`, task definitions `api:6`, `worker:11`, `frontend:3`.

Do not claim: Neo4j in production, hybrid search, causal impact, alpha, a knowledge graph of the market, production scale, Kafka HA, retrieval precision, or that 264 claims means high quality. Do not cite Phase 4.8 latency next to GPT-4o-mini. Do not cite the local 24/24 planner eval as a staging result.

Defensible technologies: FastAPI, Postgres, Alembic, Kafka (single node), S3, PySpark (batch, one-off), LangGraph, OpenAI structured outputs, ECS, Terraform, Next.js.

Buzzwords if unqualified: multi-agent, RAG, pgvector, Neo4j, distributed systems, quantitative.

### A. Software engineering

- Built a durable research job path: transactional outbox, leased worker, idempotent POST, and a Next.js desk that polls status, citations, and limitations.
- Reproduced a production planner bug (12/12 malformed JSON on staging), constrained the schema, redeployed, and measured 15/15 valid plans with the fallback unused.
- Kept market citations out of URL coverage: 113/113 document and event citations were HTTPS; 20 market citations stayed null on purpose.

### B. Data engineering

- Ingested Massive news and daily bars through Kafka and S3 into validated Parquet, then canonical Postgres: 72 articles, 264 bars, 0 failed impact rows on the successful run.
- Recorded a real schema failure: fractional volume dead-lettered until `market_bars.volume` became `NUMERIC(20,6)` (Alembic `0013`).
- Treated reruns as idempotent and wrote down a cap bug that assigned the first 50 articles to AAPL.

### C. AI engineering

- Orchestrated research as a LangGraph over a bounded evidence package, with specialist outputs checked by a deterministic verifier.
- Required the planner to emit a strict JSON schema after `json_object` mode let `evidence_requirements` through as a string.
- Shipped citation lineage to the UI: publisher, HTTPS link, or the literal text "Source URL unavailable."

### D. FinTech / quant engineering

- Computed versioned event-window returns versus SPY from unadjusted daily closes, and labeled them as session returns rather than causes.
- Served those windows beside the article URL in a research report. One staging miss: a Microsoft question attached NVDA windows. Say that if you talk about it, or fix it before you do.
- Refused recommendation language in the verifier and did not add a trading or portfolio layer.

---

## 16. GitHub / portfolio value

What is already good: `README.md` names current versus target technology and points at phase results. The phase notes are unusually honest. Tests exist. Staging was real.

What makes a recruiter close the tab:

- `ARCHITECTURE.md` still says Vercel, Railway, Chroma, and an investment memo.
- `docs/CURRENT_STATE.md` says the ingestion pipeline does not exist. It does.
- The first screen has no example report.
- The repository root mixes `.claude` and `.impeccable` leftovers with the product. They are untracked. Do not commit them. Do delete them from the working tree before anyone clones a zip.

Highest-value presentation work, in order:

1. Replace the stale architecture doc with the staging diagram from section 14.
2. Put one finished report screenshot, with a real URL, at the top of the README.
3. Add a "What this is not" box: not a predictor, not a graph of the market, not HA.
4. Archive or unroute the old investigation desk so `/` is the only story.

---

## 17. Next five improvements

### 1. Entity-correct market windows

The Microsoft staging job stored NVDA versus SPY and wrote "the stock." Fix the join, print the ticker, and add a regression test with two symbols in one package.

FinTech: this is the difference between a research tool and a wrong number. Engineering: data correctness. Recruiting: high, because you found it in production-shaped traffic. Difficulty: Medium. Payoff: high. Build now.

### 2. A labeled claim audit on the existing 15 jobs

Sample 30 claims. Mark whether the cited span supports the sentence. Publish agreement with `verify.py`. Do not tune the prompt until you have the number.

FinTech: research quality control. Engineering: evaluation. Recruiting: high for AI roles. Difficulty: Easy. Payoff: high. Build now.

### 3. Extract relationship candidates from the 72 summaries with the existing cue list

Accept none automatically unless the span and both tickers resolve. Report accepts, rejects, and co-mentions refused. Project to Neo4j only if the accept count is non-zero and you can open the source sentence.

FinTech: the product's stated graph. Engineering: provenance. Recruiting: high if the count is honest, negative if you force edges. Difficulty: Medium. Payoff: high. Build after 1.

### 4. Market-model abnormal return as calculation `v2`

Keep `v1`. Add a pre-event beta versus SPY, an abnormal return, and the session count. Null when the window is short.

FinTech and quant: the first number a markets interviewer can argue with. Engineering: medium, localized to `backend/app/market`. Recruiting: high for quant-developer, optional for general software. Difficulty: Medium. Payoff: high for quant screens only. Build if those screens matter. Do not build it instead of 1.

### 5. Make retrieval match the claim

Query pgvector in SQL or stop calling it hybrid search. Chunk something longer than the description when the provider sends it. Score Recall@5 on 20 labeled questions from this corpus with the real embedding model.

FinTech: modest. Engineering and AI: this is the actual RAG work. Recruiting: medium until the metric exists, then high. Difficulty: Medium. Payoff: medium. Build after the claim audit, because labels should be shared.

Rank: **#1 entity-correct windows, #2 claim audit, #3 relationship extract, #4 abnormal return, #5 retrieval.**

Not on the list: Neo4j-first, Airflow, Kubernetes, Grafana, another LLM provider, Fama-French, a trading simulator, a larger cluster.

---

## 18. Career-specific roadmap

| Path | Strength | Missing | Next feature | Do not build |
| --- | --- | --- | --- | --- |
| A. Software engineering co-op | 8/10 | One product, less stale docs | Entity join + README that matches staging | More services |
| B. Data engineering co-op | 7/10 | Operated quality checks, a boring backfill | Write the cap bug and the volume migration as the pipeline story; add a data-quality query | A Spark cluster |
| C. AI engineering co-op | 7.5/10 | Human eval, real retrieval metric | The 30-claim audit | Another agent |
| D. FinTech engineering | 6.5/10 | Symbol correctness, full text | Entity-correct windows | A brokerage UI |
| E. Quant developer | 4/10 | A testable return definition | Abnormal return `v2` | Execution sim |
| F. Quant research | 3/10 | A hypothesis and a universe | Stop. This repo will not become a research paper at 72 headlines | Factors, forecasts |
| G. IB technology | 5/10 | Audit trail a non-author can read | Claim audit plus the citation screenshot | A merger model inside the app |
| H. Equity research technology | 6/10 | Filing text, a single-name event page | One event page: article, window, excluded claims | Price targets |

---

## 19. Should you stop?

**ALMOST.**

If you stopped today and wrote the three bullets in section 15 without the forbidden claims, QuantPilot is already strong enough to be a flagship project for software, data, and AI co-op applications. It is not flagship for quant research or IB analyst recruiting, and more infrastructure will not change that.

Additional development stops paying when the next change does not alter a number a stranger can check. You are past that line for Kafka, Spark, Neo4j, and agent count. You are not past it for the wrong-symbol bug, a 30-claim human score, and either a real relationship extract or a written zero.

Stopping point: ship those three, update the README diagram, and freeze the architecture. A market-model `v2` is the only optional fourth item, and only if you are applying to quant-developer roles. After that, build a different project.

---

## 20. Final verdict

**OVERALL: 7/10**

**FINTECH: 6/10**

**QUANT: 4/10**

**DATA ENGINEERING: 7/10**

**AI ENGINEERING: 7.5/10**

**SOFTWARE ENGINEERING: 8/10**

**INVESTMENT BANKING TECHNOLOGY: 4/10**

**The single best thing about QuantPilot.** The system is built so a wrong model output can be seen. Staging recorded 12/12 malformed plans, the code refused to coerce the bad field, and the next staging matrix recorded 0/15 malformed and 0/15 fallback, with citations still tied to HTTPS sources.

**The single biggest weakness.** The financial object can name the wrong instrument. A Microsoft question returned NVDA windows, while the graph the README describes has zero edges. The architecture is ahead of the evidence.

**The single best next feature.** Force every market citation to the resolved ticker, and show that ticker on the card.

**The single thing not to build.** A Neo4j cluster, a factor model, or a trading simulator. Any of those would make the resume less true.

**The ideal final version.** The same staging path, one UI, a few hundred full-text articles, market rows that name the right symbol, a published human score on 30 claims, and either a handful of evidenced relationships or a still-honest zero. Calculation `v2` adds an abnormal return with a sample size. Neo4j stays off until there is something to project. The README matches that system and no longer mentions a target stack as if it were running.
