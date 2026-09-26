# QuantPilot Phase 10 — Investigation Intelligence Dashboard & Product Experience

**Scope:** Expose Phases 1–9 through the existing desk UI. Historical evaluation, deployment, CrewAI, LangGraph replacement, OpenClaw expansion, Evidence Graph redesign, trading, and a chatbot-as-home were **not** implemented.

**Source of truth:** Implementation.

---

## 1. Product goals

QuantPilot should read as an autonomous financial intelligence system:

1. What is happening in my portfolio?
2. What did QuantPilot investigate?
3. Why did it investigate?
4. What did it find?
5. How strong is the evidence?
6. What contradicts the conclusion?
7. What is happening with companies I care about?
8. What is QuantPilot currently monitoring?

The home desk (`/`) remains the investigate workbench. Phase 10 adds routes for dashboard, investigation detail, and company intelligence. No fabricated prices, activity timestamps, or conclusions.

---

## 2. Information architecture

```
Desk (/)
│
Dashboard (/dashboard)
├── Portfolio snapshot          (stored holdings / cost basis only)
├── Autonomous intelligence     (counts from ledger + alerts)
├── Recent alerts
├── Active investigations
├── Recent investigations
├── Company intelligence
└── System activity

Investigation detail (/investigations/:id)
├── Event / why investigated
├── Workflow (LangGraph stages from stored state)
├── Hypotheses
├── Evidence + provenance
├── Evidence relationships
├── Verification
├── Devil's Advocate
├── Company memory (if persisted on the case)
└── Final conclusion

Company intelligence (/companies/:ticker)
├── Overview
├── Historical investigations
├── Recurring themes
├── Risks
├── Management commentary (only with enough history)
└── Company memory timeline
```

---

## 3. Dashboard

`GET /api/dashboard` composes owner-scoped investigations, alerts, activity, and company-memory summaries.

Portfolio snapshot:

- Holdings count and tickers from the existing watchlist/holdings store.
- Cost-basis total and largest-weight only when `shares × avg_cost` exist.
- Live marks, daily P&L, and invented concentration are not computed.

Empty desk: zeros and empty lists. No placeholder movers.

---

## 4. Autonomous activity feed

Activity items are derived from stored rows:

- investigation opened / collecting / verifying / completed / skipped
- LangGraph status already persisted on the roster
- owner alerts

Timestamps are the investigation or alert timestamps already in the database. Intermediate “10:32 / 10:33 Browser MCP” filmstrips are not invented.

---

## 5. Investigation detail

`/investigations/:id` loads the existing `GET /api/investigations/{id}` payload and polls while status is `planning | collecting | verifying`.

The page shows the event, stored trigger / materiality / exposure when present, workflow rail, competing hypotheses, evidence, a simple relationship diagram, verification, Devil's Advocate, conclusion, and any company memory attached to the roster.

---

## 6. Hypothesis UI

Claims are ranked as stored. Each row shows support label from linked evidence counts (supporting vs contradicting). Expanding a hypothesis shows supporting, contradicting, and context receipts plus any stored limitation notes. Chain-of-thought is not displayed.

---

## 7. Evidence UI

Each item shows source name, title, published/retrieved dates, source type, quality badge, relevance, role relative to claims, and the real stored URL. Missing URLs stay missing.

---

## 8. Evidence graph

A simple explainability layout:

```
EVENT → leading HYPOTHESIS → evidence tiles (SUPPORT / CONTRADICT / CONTEXT)
```

Not a graph editor. Built from stored claim–evidence links.

---

## 9. Verification UI

Reads `roster.verification_summary` (or `final_report.verification`):

- claims reviewed / supported / partially supported / unsupported / contradicted
- primary-source coverage
- stored evidence gaps

No generic “AI confidence: 92%” hero number.

---

## 10. Devil's Advocate UI

Visually distinct “Challenge the Conclusion” panel from `da_outcome` and graph `da`:

- strongest counterargument
- contradicting evidence
- potential weakness
- challenge severity
- recommendation

---

## 11. Company intelligence

`GET /api/companies/{ticker}/intelligence` wraps Phase 8 `build_summary` + retrieve + owner investigations + timeline. The Phase 8 `GET /api/investigations/memory/{ticker}` endpoint remains.

---

## 12. Company memory timeline

Chronological grouping of stored memory items by month. Months without stored items are omitted.

---

## 13. Management trends

The management commentary block renders only when Phase 8 `detect_management_trend` returns a string **and** at least two `MANAGEMENT_STATEMENT` items exist. Otherwise the UI says historical data is insufficient.

---

## 14. Alerts

Dashboard alert cards show ticker, stored title/message, materiality if present, investigation link, company link, dismiss (mark read), and Useful / Not useful feedback. No BUY / SELL / EXECUTE.

---

## 15. Active investigations

Active cases (`planning | collecting | verifying`) appear on the dashboard with ticker, stored move, status/stage, and start time. The detail page marks the current workflow step and current task from stored research-plan objectives when present.

---

## 16. API requirements

Additive only:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/dashboard` | Owner-scoped desk snapshot |
| GET | `/api/companies/{ticker}/intelligence` | Company memory + investigations |
| POST | `/api/alerts/events/{event_id}/feedback` | `useful` / `not_useful` / `""` |

Existing investigation, evidence, memory, and alert list endpoints are unchanged. Authorization: signed-in user **or** `X-Guest-Id`. Alert feedback requires the authenticated owner.

---

## 17. Security

- Dashboard and company intelligence require an owner key.
- Investigations remain 404 across owners (ticker/id alone is not enough).
- Alert feedback is `user_id` scoped.
- Guests see their guest-scoped investigations; watchlist/alerts remain signed-in surfaces.

---

## 18. Performance

Dashboard lists are capped (recent 12, active 8, activity 24, alerts 8, company updates 6). Company timeline is capped at 24 items. Investigation detail reuses the existing case poller and does not load every historical investigation on first paint.

---

## 19. Testing

Backend: `backend/tests/test_investigation_phase10_dashboard.py`

- dashboard empty state
- owner isolation
- investigation 404 across guests
- company intelligence from stored memory only
- legacy memory endpoint compatibility
- alert feedback owner-scoped
- existing `/api/investigations` list compatibility

Frontend: no Jest/Vitest runner in this repo. Phase 10 checks are `tsc --noEmit`, `next lint`, and `next build`.

---

## 20. Routes

| Route | Role |
|---|---|
| `/` | Investigate desk (unchanged workbench) |
| `/dashboard` | Intelligence dashboard |
| `/investigations/[id]` | Investigation detail |
| `/companies/[ticker]` | Company intelligence |

The desk rail adds Dashboard. An open case on `/` links to Full investigation, Company intelligence, and Dashboard.
