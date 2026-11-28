# QuantPilot Phase 6 — Primary-source acquisition (Browser MCP + OpenClaw)

**Scope:** Targeted official-website research when structured APIs are insufficient. Company memory, historical evaluation, CrewAI, frontend redesign, LangGraph replacement, and a generic web scraper were not implemented.

**Source of truth:** Implementation.

---

## 1. Why OpenClaw exists

OpenClaw is in QuantPilot because some primary sources live on websites, not in a clean API: investor presentations, earnings decks, shareholder letters, official IR pages, and PDFs that must be found by navigating a company site.

It is **not** a search engine. It does not Google “why did NVDA fall?” and summarize the web.

---

## 2. API vs browser decision

```
Structured data available? ──YES──► MCP API (price, news, filings, fundamentals)
        │
        NO
        ▼
Official primary source on a website? ──YES──► Browser MCP ──► OpenClaw (only if httpx cannot get the document)
        │
        NO
        ▼
Record an evidence gap. Do not fabricate.
```

| Need | Tool |
|---|---|
| Price / history / fundamentals | Finance MCP |
| SEC filing | SEC MCP |
| News | News MCP |
| IR homepage / letter | `get_ir_materials` / `get_shareholder_letter` (httpx first) |
| Specific official deck/PDF after IR is incomplete | `browser_find_document` |

The LLM cannot pick OpenClaw because it is available.

---

## 3. Browser MCP architecture

```
LangGraph / investigation runner
        ↓
MCP client  (call_tool)
        ↓
Browser MCP  (browser_search | browser_open | browser_find_document | browser_download | browser_extract)
        ↓
allowlisted httpx   or, if needed, OpenClaw
        ↓
Official website
        ↓
Existing Evidence Ledger
```

Investigation code never issues raw OpenClaw commands.

---

## 4. OpenClaw architecture

Unchanged adapter: `mcp_server/tools/openclaw_client.py`.

- **HTTP** (`OPENCLAW_BROWSER_URL`): `/start`, `/navigate` or `/tabs/open`, `/snapshot`
- **CLI** (opt-in `OPENCLAW_USE_CLI`): `openclaw browser open` + `snapshot`
- **Existing-session snapshot**: portfolio paste-alternative only. No investigation navigation. No brokerage credentials.

`get_ir_materials` still prefers httpx. `OPENCLAW_IR_FALLBACK` (default **false**) still gates OpenClaw on generic IR page fetch.

`browser_find_document` may call OpenClaw **only after** official IR HTML did not yield the requested document **and** OpenClaw is configured. If OpenClaw is down, the tool returns a structured failure.

---

## 5. Tool schemas

| Tool | Inputs | Output |
|---|---|---|
| `browser_search` | `domain`, `query`, `max_results`, `ticker` | candidate titles/URLs on that official domain |
| `browser_open` | `url`, `ticker` | title, URL, sanitized text |
| `browser_find_document` | `ticker`, `document_type`, `query`, `company`, `domain` | selected official document + extract |
| `browser_download` | `url`, `ticker` | metadata + extracted text or `extraction_failed` |
| `browser_extract` | `text`, `objective`, `source_url`, `title` | relevant window of already-retrieved text |

Existing tools `get_ir_materials`, `fetch_browser_page`, `get_shareholder_letter`, `snapshot_active_browser_tab` remain.

---

## 6. Routing policy

`backend/app/services/tool_router.py`:

1. Price / news / filings / fundamentals → API tools.
2. Generic primary source → `get_ir_materials`.
3. `PRIMARY_SOURCE_DOCUMENT` → `get_ir_materials` first; `browser_find_document` only after IR has been tried and no presentation is in the ledger.
4. Browser contract tools are capped (`MAX_BROWSER_TASKS_PER_INVESTIGATION`, default 2) and de-duplicated.

The planner still emits `get_ir_materials` for large/downside equity moves. It does **not** put `browser_*` on the first-pass tool list. The Phase 3 gap loop adds `browser_find_document` when GUIDANCE/EARNINGS still needs an official deck.

---

## 7. Primary-source acquisition workflow

1. Investigation planner: company-specific move → `get_ir_materials` (category `primary_source`).
2. Phase 4 GUIDANCE hypothesis requires “investor presentation”.
3. IR homepage retrieved via httpx (or existing IR tools).
4. If no presentation/PDF is present, assessment recommends `browser_find_document`.
5. Browser MCP resolves official IR hosts for the ticker (not NVIDIA-only).
6. Parses official pages for candidate documents.
7. If none and OpenClaw is configured, navigates the official IR site.
8. Validates domain. Rejects blogs.
9. Selects the document that matches type/date/title.
10. Downloads / extracts when supported.
11. Maps into the **same** Evidence Ledger (`source_type=ir_page`, `retrieval_method=browser_mcp` when OpenClaw was used).
12. Claim links and Phase 5 support assessment run unchanged.

---

## 8. Document discovery

`browser_find_document` visits at most `MAX_BROWSER_PAGES_PER_TASK` official IR seeds (`_KNOWN_IR_URLS` plus `investor.` / `ir.` hosts from the company website). It extracts links from HTML or OpenClaw snapshot text. It does not crawl the internet.

---

## 9. Document selection

Deterministic score:

- official domain
- title / URL match to requested type (`earnings_presentation` vs `annual_report`)
- ticker / company
- PDF
- date hint

A request for the latest earnings presentation will not prefer an annual report. Off-domain blogs cannot win.

---

## 10. Evidence integration

No second store. Browser rows use existing `evidence_items`.

| Field | Typical browser-acquired document |
|---|---|
| `source_type` | `ir_page` (Phase 5 taxonomy: `PRIMARY_COMPANY`) |
| `retrieval_method` | `browser_mcp` (OpenClaw) or `httpx` (API-first hit) |
| `source_url` | official URL |
| `title` | document title |
| `excerpt` | extracted text, or an explicit extraction-failure note |
| `raw_payload` | provider, document_type, published_at, tool |

Phase 5 hashing / provenance / quality apply on persist.

---

## 11. Provenance

Preserved: original URL, domain, title, document URL, retrieval timestamp, publication hint, retrieval method, extraction outcome, content hash of the excerpt.

---

## 12. Security

- OpenClaw investigation profile does not receive brokerage credentials, OpenAI keys, or database secrets.
- Gateway token / password stay in process environment headers only; they are never logged and never stored on evidence.
- Portfolio “existing session” snapshot is unchanged and still does not navigate.
- Cookies are not persisted by QuantPilot.
- Investigation tools only open allowlisted official hosts (company IR, SEC, `.gov`, known IR CDNs).

---

## 13. Prompt-injection defense

Webpage text is **data**. `sanitize_web_text` strips instruction-like lines (`ignore previous instructions`, `reveal your API key`, `system prompt`). Extracted excerpts are never written into a system prompt. A page cannot change routing, tools, or secrets.

---

## 14. Timeouts

| Stage | Variable | Default |
|---|---|---|
| MCP browser tool | `MCP_BROWSER_TIMEOUT_SECONDS` | 45 |
| OpenClaw navigate | `BROWSER_NAV_TIMEOUT_SECONDS` | 20 |
| Discovery | `BROWSER_DISCOVERY_TIMEOUT_SECONDS` | 30 |
| Extract / PDF download | `BROWSER_EXTRACT_TIMEOUT_SECONDS` | 15 |

---

## 15. Retries

`MAX_BROWSER_RETRIES` (default 1) applies only to transient connection/load failures (`timeout`, `503`, reset). Invalid URLs, rejected hosts, 404-style “not found”, and blocked sites are not retried.

MCP client still does **not** retry browser tools at the transport layer (one shot).

---

## 16. Resource limits

| Limit | Default |
|---|---|
| Browser contract tools per investigation | 2 |
| Official pages per find-document task | 4 |
| Evidence rows still capped at 24 |

No unlimited browse loop.

---

## 17. Failure handling

```
Browser failure
    → structured error (no invented excerpt)
    → ledger row marked failed / system
    → assessment records a gap
    → investigation may continue
    → report discloses incomplete primary-source evidence
```

PDF extract failure records `extraction_failed`. The URL may still be kept; contents are never guessed.

---

## 18. Observability

Events (no cookies, tokens, or secrets):

`BROWSER_TASK_STARTED`, `BROWSER_TASK_COMPLETED`, `BROWSER_TASK_FAILED`, `BROWSER_DOCUMENT_FOUND`, `BROWSER_DOCUMENT_SELECTED`, `BROWSER_DOCUMENT_DOWNLOADED`, `BROWSER_CONTENT_EXTRACTED`, `BROWSER_SOURCE_VALIDATED`, `BROWSER_SOURCE_REJECTED`

Fields include ticker, domain/URL, tool, status, latency, retrieval method.

---

## 19. Testing

`backend/tests/test_investigation_phase6_browser.py` uses mocks only (no live websites):

- API vs browser routing
- search / open / find / select / PDF extract
- official vs blog domain
- empty content, timeout, not found
- prompt-injection sanitization
- ledger provenance + claim link
- planner primary-source task
- Phase 4 GUIDANCE still asks for a presentation
- flagship NVDA mocked investigation

---

## 20. Flagship demo

**Event:** NVDA falls 8.7%.

**Hypothesis:** Guidance concerns (requires latest official investor presentation).

**Expected (mocked in tests, same path in production):**

1. Planner schedules `get_ir_materials` (API/httpx IR).
2. IR homepage is not the deck → assessment recommends `browser_find_document`.
3. Browser MCP targets `investor.nvidia.com` (official).
4. If HTML has no PDF and OpenClaw is configured, OpenClaw navigates that IR site.
5. Candidates are scored; Q2 FY2027 Earnings Presentation beats an annual report and any blog.
6. Domain validated. Text extracted or failure recorded.
7. Evidence lands in the existing ledger with `retrieval_method=browser_mcp`.
8. The guidance claim is linked. Verification / Devil’s Advocate consume the Phase 5 graph.
9. The report can cite the official URL. Nothing is invented.

---

## What Phase 6 did not implement

- Company memory
- Historical evaluation
- CrewAI
- Frontend redesign
- LangGraph rewrite
- Evidence Graph rewrite
- Generic web scraper / search engine / CAPTCHA solver / RPA
- OpenClaw as the default data source
- Live-website tests in CI
