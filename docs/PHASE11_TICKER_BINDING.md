# Phase 11 — ticker binding

A Microsoft research question on the Phase 10.1 staging corpus returned NVDA market windows. The impact rows were real. They belonged to NVIDIA.

## Root cause

`POST /api/research` was sent with the query text only. `entity_ids` and `event_ids` stayed empty.

`assemble_package` then called `load_events` with no ids. That returns the latest confirmed events in the whole corpus, not events about the company named in the question. `load_market_facts` loaded every `event_impacts` row for those events and did not take an entity or a symbol. A Microsoft question could therefore cite an NVDA window. The market specialist wrote “the stock,” and the desk printed `market.symbol`.

## Where identity lives

| Step | What identifies the company |
| --- | --- |
| Request | Query text, or `entity_ids` when the caller sends them |
| Company row | `companies.entity_id`, `companies.ticker`, `companies.exchange` |
| Event | `event_entities` row. A name in the article is not a link |
| Impact | `event_impacts.entity_id` and `event_impacts.symbol` already stored together |
| Citation | `market.entity_id`, `market.symbol`, `market.canonical_name`, benchmark, `event_id`, `calculation_version`, window definition |
| Report | `report.metadata.resolved_securities`, checked again before the report row is written |

The ticker is introduced from `companies.ticker` inside `resolve_securities`. It is not taken from model output. `load_market_facts` is the retrieval step. The wrong symbol entered there, because the function accepted every impact for the selected events.

## Fix

`resolve_securities` maps explicit entity ids, or a whole-word ticker / legal-name stem in the query, to `{entity_id, symbol, exchange, security_type, canonical_name}`. The symbol is the company row’s ticker. An unknown name does not fall through to another company. An explicit `entity_ids` list is not widened by other names in the question.

`load_market_facts` and the `get_market_impact` tool require that resolved list. A row is kept only when the impact entity and symbol are one resolved security and `event_entities` links that entity to the event. Anything else is dropped. It is not rewritten onto the other symbol.

`validate_report_market_bindings` runs at the end of `assemble_report` and again in `persist_result`. A market citation that fails the same pair check is removed, its observation is removed, and the report records a limitation.

Market citations still have `source_url: null`. URL coverage is still document, event, and graph citations only.

The desk prints `Microsoft (MSFT)` and `Relative return vs SPY` when `entity_id`, symbol, and benchmark are present and `identity_verified` is true. Otherwise it prints “Market impact unavailable: security identity could not be verified.” and does not render the numbers.

## Tests

`backend/tests/unit/research/test_ticker_binding.py`

1. Microsoft query, MSFT and NVDA impacts present → only MSFT.
2. NVIDIA query → only NVDA.
3. Microsoft event with an NVDA impact → no market fact; a forged report citation is stripped.
4. Microsoft event with an MSFT impact → kept, including when an NVDA row sits on the same event.
5. Microsoft entity with an NVDA symbol on the impact → `symbol_mismatch`, nothing returned.
6. NVIDIA entity with an MSFT symbol → `entity_mismatch`, nothing returned.
7. No resolvable security → no market facts, the unverified limitation, and `get_market_impact` returns no rows.
8. An article that names Microsoft, OpenAI, and NVIDIA selects a window only after an `event_entities` row exists, and only for that entity.

The same file runs the four research questions through `run_research` with `FakeLLMProvider` and checks resolved entity, symbol, impact entity, and report symbol.

## Schema

No migration. `event_impacts` already stores `entity_id` and `symbol`.
