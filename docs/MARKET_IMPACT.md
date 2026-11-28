# Market impact

Descriptive event-window analytics. Not a forecast, not attribution, not investment advice.

## Inputs

Canonical `market_bars` only. v1 uses unadjusted `close`. `adj_close` and currency are stored on bars when the vendor sent them; they are **not** used in v1.

Confirmed events only. Entity → symbol is `companies.ticker`, else the first of `companies.tickers`.

## calculation_version = `v1`

| Metric | Formula |
|---|---|
| `return_pN` | `close(T+N) / close(T0) - 1` |
| `return_m1` | `close(T0) / close(T-1) - 1` |
| `relative_return` | asset return − benchmark return (same window) |
| Simple abnormal-return proxy | same as `relative_return` — not CAPM, not causal |
| `volume_ratio` | event-day volume / mean volume of 20 prior sessions |
| `pre_event_volatility` | sample stdev of daily close-to-close returns among the 20 prior sessions |
| `event_window_volatility` | sample stdev of daily returns from T0 through T+5 |

Missing bars → `NULL`. Zero or negative prices → unavailable, never coerced to 0.

Benchmark symbol: `MARKET_BENCHMARK_SYMBOL` (default `SPY`). No silent substitute.

## Identity / lineage

Unique `(event_id, entity_id, symbol, calculation_version)`. Re-run of `v1` updates the same row. A future `v2` is a new row.

Lineage: `first_market_date`, `last_market_date`, `calculation_job_id`. Market payloads stay in `market_bars`.

## Commands

```
python -m app.market events-from-news
python -m app.market impact [EVENT_ID] [--since DATE] [--until DATE]
python -m app.market quality
```
