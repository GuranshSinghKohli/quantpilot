# Trading calendar

Windows are **trading sessions**, not calendar days. The calendar for a symbol is the distinct `market_bars.trading_date` values that actually exist for `interval='1d'`.

## Event trading date (T0)

1. Timestamp = `occurred_at` if set, else `published_at`.
2. Calendar date = that timestamp in **UTC**.
3. If a bar exists on that date → T0.
4. Else T0 = the next later session (weekend / holiday).
5. After-hours is not distinguished: daily bars have no session clock.

Publication time is not pretended to be occurrence time.

## Offsets

| Label | Meaning |
|---|---|
| `-1D` | previous session |
| `+1D` | next session |
| `+3D` | three sessions later |
| `+5D` | five sessions later |
| `+20D` | twenty sessions later |

Missing sessions are unavailable. Prices are never forward-filled.

## Limitations

- UTC date, not an exchange holiday file.
- Newest ingested bar wins when two vendors share a symbol/date.
- Invalid OHLC / negative volume bars are dropped from the calendar.
