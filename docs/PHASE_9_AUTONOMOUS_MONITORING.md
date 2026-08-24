# QuantPilot Phase 9 — Autonomous Monitoring & Intelligent Alerting

**Scope:** Make QuantPilot continuously monitor holdings and decide whether to investigate and whether to notify. Historical evaluation, frontend redesign, CrewAI, OpenClaw expansion, Evidence Graph redesign, a new orchestrator, trading, and deploy/productionization were **not** implemented.

**Source of truth:** Implementation.

---

## 1. Purpose

```
SCHEDULED MONITOR
        ↓
MARKET / EVENT DATA
        ↓
EVENT DETECTION
        ↓
MATERIALITY
        ↓
DEDUPLICATION / COOLDOWN
        ↓
SHOULD INVESTIGATE?          (reopen if the story changed)
      ↙       ↘
    NO         YES
    ↓           ↓
record      LangGraph investigation
                ↓
           verification
                ↓
          final assessment
                ↓
         SHOULD NOTIFY?
            ↙       ↘
          NO         YES
          ↓           ↓
       record       alert
```

The monitor answers six questions:

1. Did something unusual happen?
2. Is it material enough to spend research?
3. Should we investigate?
4. If we already investigated recently, is this the same story?
5. Has enough changed to reopen?
6. After a complete case, is it worth notifying the user?

---

## 2. Architecture

Phase 9 is a **decision layer**, not a second pipeline.

| Step | Existing module | Phase 9 addition |
|---|---|---|
| Schedule | `scheduler.py` + `investigation_sweep_job.py` | Default-on (capped); lightweight/tests stay off |
| Detect | `event_detection.py` / `trigger_logic.py` | Unchanged |
| Investigate bar | `materiality.assess_investigation_materiality` | Unchanged |
| Cooldown | ledger 6h + event fingerprint | **Reopen exception** |
| Run | Phase 3 LangGraph via `investigate_ticker` | Unchanged |
| Notify bar | `materiality.assess_materiality` | Smarter suppressions + memory note |

`backend/app/services/autonomous_monitor.py` owns reopen and notify decisions.

---

## 3. Scheduled monitor

`INVESTIGATIONS_SWEEP_ENABLED` defaults to **`true`**.

Hardening:

- Tests / CI / `QUANTPILOT_LIGHTWEIGHT` keep the sweep **off**.
- Cap: `INVESTIGATION_SWEEP_MAX_PER_RUN` (default 5, max 50).
- Interval clamp 5–240 minutes (default 30).
- `max_instances=1`, `coalesce=True`.
- In-flight cases (`planning` / `collecting` / `verifying`) are never piled on.
- Sweep still uses signed-in users with holdings only (not the shared anonymous watchlist).

---

## 4. Dedup vs reopen

Cooldown still applies when the new event is the **same story**.

Reopen (bypass cooldown) only when something **materially changed**:

| Change | Example |
|---|---|
| Event family | Prior price drop; new 8-K / earnings |
| Magnitude | Prior −5.0%; now −9.2% (≥ 3pp larger) |
| Direction | Prior −4%; now +4% |
| Memory conflict | Management statement marked `OUTDATED` since the last case |
| Failed prior | Last case failed / noise-skip; new filing or earnings |

Do **not** reopen:

- Prior investigation still in flight
- Same family + similar magnitude (e.g. −8.7% then −8.5%)
- Same event fingerprint in the current time bucket with no change

Language: this is a **reopen**, not “the same investigation.”

---

## 5. Notify

Still **after** a complete proactive case (`scheduled` / `price_move` / `alert`).

Still require the Phase 12 materiality bar.

Additional suppressions:

- No signed-in user
- On-demand / manual
- Incomplete / failed / noise-skip
- No grounded claims
- Leading claim stance is `market_noise`
- Notify cooldown already used

If a notification is created, the optional company-memory management trend may be appended as context. It is not a causal claim.

---

## 6. User isolation

Sweep and notify use `user:{id}` only.

Guest device memory / investigations do not leak into another user’s monitor cycle.

Portfolio weight is used for **investigation** materiality (who is exposed). It is not stored in company memory and is not trade advice.

---

## 7. APIs

Additive only:

- `GET /api/investigations/trigger/{ticker}` now includes `reopen`, `reopen_reason`, `prior_investigation_id`.
- `POST /api/investigations/sweep` summary includes `reopened`, `skipped_unchanged`.

Existing clients ignore unknown fields.

---

## 8. Observability

| Event | When |
|---|---|
| `MONITOR_STARTED` | Sweep begins |
| `MONITOR_COOLDOWN` | Same story / in-flight |
| `MONITOR_REOPEN` | Cooldown bypassed |
| `MONITOR_LAUNCH` | Investigation started |
| `MONITOR_NOTIFY` | In-app alert created |
| `MONITOR_NOTIFY_SUPPRESSED` | Alert skipped |
| `MONITOR_COMPLETED` | Sweep finished |

No secrets, no brokerage data.

---

## 9. Testing

`backend/tests/test_investigation_phase9_autonomous_monitoring.py` covers reopen, similar-event suppression, in-flight, memory-change reopen, notify gates, gate integration, sweep short-circuit, and notify cooldown.

Existing Phase 2 / 11 / 12 cooldown and notify tests remain the regression anchors.

---

## 10. Example

**COMPANY:** NVDA

1. Monitor sees an 8.7% idiosyncratic drop → investigate → complete → notify if the bar clears.
2. Six hours later, another −8.5% print → **cooldown** (“no material change”).
3. An 8-K arrives while that cooldown is still active → **reopen** (“new event type FILING”).
4. LangGraph investigates with company memory. Current evidence wins.
5. Notify only if the completed case is material and has grounded claims.

Do not claim the filing *caused* the first drop unless the Evidence Graph supports that.

---

## What Phase 9 did not implement

- Historical evaluation / benchmark (later)
- Frontend redesign / alert inbox UI
- CrewAI / new orchestration framework
- OpenClaw expansion
- Evidence Graph redesign
- Autonomous trading or brokerage execution
- Production deploy
