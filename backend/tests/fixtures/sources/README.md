# Source adapter fixtures

Test inputs for `tests/unit/pipeline/test_source_*.py` and `tests/kafka/test_source_ingestion.py`. Nothing here is ever loaded into a development database or presented as data.

| File | Origin |
|---|---|
| `massive_news.sample.json` | The sample response published in the Massive docs for `GET /v2/reference/news` (https://massive.com/docs/rest/stocks/news), copied verbatim on 2026-09-27 |
| `massive_aggs.sample.json` | The sample response published in the Massive docs for `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}` (https://massive.com/docs/rest/stocks/aggregates/custom-bars), copied verbatim on 2026-09-27. Its `adjusted` is `true` (the docs' default); the adapter requests `adjusted=false` |
| `sec_company_tickers_exchange.subset.json` | Nine real rows (NVIDIA, Apple, Alphabet ×4 share classes, Microsoft, Berkshire Hathaway ×2) filtered from `https://www.sec.gov/files/company_tickers_exchange.json`, captured 2026-09-27 |

Error responses, pagination sequences and malformed items are built in test code (`tests/unit/pipeline/source_harness.py`) and named as such.
