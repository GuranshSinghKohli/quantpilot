# QuantPilot Phase 2 — Event Detection and Materiality

**Scope:** Pre-investigation event detection and gating. The investigation runner, LangGraph research graph, OpenClaw, CrewAI, and Evidence Ledger schema were not changed.

---

## 1. Event model

Internal Pydantic model `MarketEvent` in `backend/app/models/event_schemas.py`.

Not persisted. No new database table.

| Field | Meaning |
|---|---|
| `event_id` | Stable-enough id (`ticker:TYPE:…`) |
| `ticker` | Symbol |
| `event_type` | Extensible enum |
| `detected_at` / `occurred_at` | When we saw it / when it happened |
| `direction` | `UP` / `DOWN` / `NONE` |
| `magnitude` | Price % or earnings surprise % when known |
| `benchmark` | Configured benchmark (default SPY) |
| `z_score` / `residual_pct` | Vol-adjusted and idiosyncratic significance |
| `portfolio_exposure` | Book weight 0–1 |
| `severity` | `LOW` / `MEDIUM` / `HIGH` |
| `source` | MCP tool name or trigger source |
| `reason` | Human-readable detection note |
| `metadata` | Filing type, earnings date, etc. |

---

## 2. Event types

| Type | Implemented? | Detector |
|---|---|---|
| `PRICE_MOVE` | Yes | Existing `evaluate_trigger` / `detect_move` |
| `IDIOSYNCRATIC_MOVE` | Yes | Residual vs configured benchmark ≥ 1.0% |
| `VOLATILITY_SPIKE` | Yes | \|z\| ≥ `INVESTIGATION_VOL_Z_THRESHOLD` |
| `EARNINGS` | Yes | MCP `get_stock_fundamentals` earnings date in a short window |
| `FILING` | Yes | MCP `get_recent_filings` for 8-K / 10-K / 10-Q / 6-K / 20-F |
| `VOLUME_SPIKE` | Enum only | Not implemented (no dedicated reliable signal beyond price history) |
| `NEWS` | Enum only | Not a first-class detector (news remains an investigation tool) |
| `MACRO` | Enum only | No dedicated MCP macro calendar |

All external I/O stays on MCP. Earnings dates were added to the **existing** `get_stock_fundamentals` tool (Yahoo `info` timestamps). Surprise % is optional (`earningsQuarterlyGrowth` when present) and is not invented.

---

## 3. Detection logic

```
evaluate_trigger (price, vol, residual, depth)
        ↓
events_from_trigger → PRICE_MOVE / IDIOSYNCRATIC_MOVE / VOLATILITY_SPIKE
        ↓
get_recent_filings → FILING (lookback, material forms only)
        ↓
get_stock_fundamentals → EARNINGS (lookahead/lookback window)
```

Price detectors reuse Phase 11 `trigger_logic._decide`. They were not removed.

`QUANTPILOT_LIGHTWEIGHT=true` skips live filing/earnings MCP calls (tests / constrained deploys). The normalizers `events_from_filings` / `events_from_fundamentals` still run in tests with fixtures.

---

## 4. Materiality formula

Two scores. They answer different questions.

### Investigation materiality (new)

`assess_investigation_materiality(event)` — **before** the runner.

```
score =
    event_type_base
  + min(35, |z| × 10)
  + min(30, |residual| × 5)
  + min(15, |move%| × 1.5)
  + min(25, exposure × INVESTIGATION_EXPOSURE_WEIGHT)

If the event is a price-family move with residual < INVESTIGATION_IDIO_PCT
and |move| < max(3, 3×idio bar):
    score *= 0.35
    should_investigate = false   # market-wide
else:
    should_investigate = score >= INVESTIGATION_MATERIALITY_THRESHOLD
```

Event-type bases (approximate): 8-K 42, other material filings 30–38, earnings 38, idiosyncratic 8, vol spike 6, raw price 4.

Depth from score (and HIGH severity): `<45` light, `≥45` standard, `≥70` deep.

### Notification materiality (unchanged)

`assess_materiality(move)` after a **complete** proactive investigation. Same Phase 12 formula and gates (`INVESTIGATION_NOTIFY_*`).

---

## 5. Portfolio exposure

```
weight = (shares × mark) / Σ(shares × mark)
mark   = current MCP price for the ticker under review, else avg_cost
```

Watchlist rows without shares contribute 0. An empty sized book yields weight 0 (no invented equal-weight). Exposure **boosts** the investigation score; it does not generate trade advice.

`event_impact ≈ |magnitude| × weight` (percent-of-portfolio points) is recorded on the score for explainability.

---

## 6. Investigation gate

`evaluate_investigation_gate` in `backend/app/services/investigation_gate.py`:

1. Detect events (MCP).
2. Attach portfolio weight.
3. Ledger cooldown (`recent_investigation_exists`, default 6h).
4. Event fingerprint cooldown (`ticker|type|direction|time_bucket` in Redis/memory).
5. Pick primary event (filing / earnings beat beta noise).
6. Score investigation materiality.
7. Pass → sweep may launch the **existing** runner. Fail → skip with a reason.

The scheduled sweep now uses this gate **before** `investigate_ticker`. On-demand `POST /api/investigations/investigate` is unchanged (user asked).

---

## 7. Notification gate

Unchanged path:

```
investigation complete
  → trigger_reason in {scheduled, price_move, alert}
  → assess_materiality(move)
  → cooldown on investigation_material alerts
  → in-app AlertEvent
```

On-demand / manual cases still do not notify.

---

## 8. Cooldown / dedup

| Layer | Key | Default |
|---|---|---|
| Ledger | `owner_key + ticker` in last N hours | 6h (`INVESTIGATION_COOLDOWN_HOURS`) |
| Fingerprint | `ticker\|event_type\|direction\|UTC-day-bucket` | same TTL in Redis / in-memory cache |
| Notify | user + ticker + `investigation_material` | 6h |

Fingerprint buckets are `INVESTIGATION_EVENT_BUCKET_HOURS` wide (default 6). A later 8-K on the same name can still pass if the ledger cooldown has expired; while a case is fresh, the ledger cooldown wins.

---

## 9. Configuration

| Variable | Default | Role |
|---|---|---|
| `INVESTIGATION_MATERIALITY_THRESHOLD` | 40 | Pre-investigate score bar |
| `INVESTIGATION_PRICE_MOVE_THRESHOLD` | 2.5 | Informational reject log for tiny prints |
| `INVESTIGATION_VOL_Z_THRESHOLD` | 1.5 | Vol spike + existing trigger |
| `INVESTIGATION_IDIO_PCT` | 1.0 | Residual bar / market-wide haircut |
| `INVESTIGATION_COOLDOWN_HOURS` | 6 | Ledger + fingerprint TTL |
| `INVESTIGATION_EXPOSURE_WEIGHT` | 100 | Points at 100% book weight |
| `INVESTIGATION_EARNINGS_LOOKAHEAD_DAYS` | 2 | Upcoming print window |
| `INVESTIGATION_EARNINGS_LOOKBACK_DAYS` | 1 | Recent print window |
| `INVESTIGATION_FILING_LOOKBACK_DAYS` | 3 | Fresh filing window |
| `INVESTIGATION_EVENT_BUCKET_HOURS` | 6 | Fingerprint time bucket |
| `INVESTIGATION_NOTIFY_*` | unchanged | Post-investigation notify |

---

## 10. Test strategy

`backend/tests/test_investigation_phase2_events.py` covers:

- Existing `_decide` skip/fire (normal, market-wide, idiosyncratic)
- Price / idio / vol event normalization
- Filing 8-K vs stale / Form 4
- Earnings in-window vs out-of-window
- Low vs high portfolio weight
- Investigation score: investigate / skip / 8-K / below-bar
- Fingerprint new vs duplicate
- Gate pass / fail / 8-K-without-price / ledger cooldown
- Notification materiality still separate; on-demand still silent

Phase 11 sweep/preview tests now mock `evaluate_investigation_gate` (additive fields only). Phase 12 notify tests still call `assess_materiality`.

---

## 11. Example decisions (actual formula)

### Example 1 — investigate

NVDA −8%, SPY −1% (residual −7%), z = −4, book weight 22%, type `IDIOSYNCRATIC_MOVE`.

```
8 (type) + 35 (z cap) + 30 (residual cap) + 12 (move) + 22 (exposure) = 100
depth = deep
→ INVESTIGATION_GATE_PASS
```

Reason shape: `Investigating NVDA because: IDIOSYNCRATIC_MOVE; score=100.0; depth=deep; move=-8.00%; residual=-7.00%; z=-4.00; exposure=22.0%`.

### Example 2 — skip

NVDA −1.2%, SPY −1.0% (residual −0.2%), z = −0.6, weight 2%, type `PRICE_MOVE`.

Market-wide haircut applies (`residual < 1.0%` and small absolute move). `should_investigate = false`.

Reason shape: `Skipped NVDA: movement tracks the benchmark (residual -0.20% < 1.0% idiosyncratic bar)…`

### Example 3 — investigate

NVDA 8-K filed yesterday. No usable price residual required.

```
42 (8-K base) + 0 price terms + 0 exposure = 42 ≥ 40
→ INVESTIGATION_GATE_PASS
```

A Form 4 or a 10-Q older than the lookback does **not** create an event.

---

## Observability

Structured log messages (via existing `log_event`):

- `EVENT_DETECTED`
- `EVENT_REJECTED`
- `INVESTIGATION_GATE_PASS`
- `INVESTIGATION_GATE_FAIL`
- `EVENT_COOLDOWN`

No secrets are logged.

---

## What Phase 2 did not do

- No LangGraph investigation orchestrator
- No company memory
- No Evidence Graph schema change
- No CrewAI / OpenAI Agents SDK
- No new OpenClaw / Browser MCP tools
- No frontend redesign
- No rewrite of `investigation_runner.py`
