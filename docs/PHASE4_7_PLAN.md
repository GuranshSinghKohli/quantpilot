# Phase 4.7 — Claim coverage and evidence-to-finding reliability

Phase 4.6 is unchanged at `5c651a613186e2312841097693e60c1520eaf6d7`. Alembic head stays `0012`. No migration.

These scores are DEVELOPMENT-ONLY, FIXTURE-BASED, NOT HUMAN-JUDGED, and NOT PRODUCTION MODEL QUALITY. FakeLLM quotes fixture fields. It is not a production model.

## What Phase 4.6 measured

Finding-type recall (`claim_recall`), after the planner stopped reading package text:

- 24-case baseline: precision 1.0, recall 0.8824, F1 0.9375
- 50-case set: precision 1.0, recall 0.9167, F1 0.9565

Phase 4.5 finding-type recall was 1.0 on both sets.

## Where the claims stopped

Two queries lost a FACT finding type.

| Query | Stage last seen | Reason |
| --- | --- | --- |
| Meta data center accelerators | specialist finding | The technology agent was selected. The package excerpt says "deploy Instinct accelerators". The fixture reader skipped any excerpt that did not contain "technolog", "chip", or "gpu". Verification and report assembly never saw a finding. |
| AMD Instinct Meta data centers | evidence package | `AGENT_NOT_SELECTED`. The request has no domain vocabulary. Phase 4.6 `required_agents` is empty. The partnership document is in the fixture, and no specialist is selected to read it. |

The second row is not an unsupported document. It is a request the router does not assign. Selecting news because the package contains an article would undo Phase 4.6. The expected FACT type was not deleted, so finding-type recall still counts this miss.

## Fix

The fixture reader quotes a document when the excerpt names a technology, chip, GPU, or accelerator. The statement is `Source describes technology:` plus the excerpt. It does not emit `Documented USES` or a canonical relationship.

A sentence that only discusses a technology is quoted as discussion. A co-mention with no technology term produces no technology finding. Mention is not USES, not a partnership, and not a customer edge.

The technology prompt states the same rule. The planner, date filter, worker, and outbox are unchanged.

## How the two recalls differ

`claim_recall` is still finding-type overlap, including the Instinct case.

`supported_claim_recall` counts explicit facts a selected specialist should quote. The Instinct case is `agent_not_selected_omissions`, not a supported-claim miss and not an unsupported claim.

`correctly_omitted_unsupported_claims` counts cases whose expected finding-type list is empty. Those cases do not reward an invented claim.

## Measured after the reader change

24-case baseline:

- finding-type precision 1.0, recall 0.9412, F1 0.9697 (Phase 4.6 was 1.0 / 0.8824 / 0.9375)
- supported-claim precision 1.0, recall 1.0, F1 1.0
- supported expected claims 16, produced 16
- correctly omitted unsupported claims 7
- agent-not-selected omissions 1 (`amd-news-only`)
- evidence support rate 1.0
- unsupported final claim rate 0.0
- finding-type preservation 1.0
- citation lineage 1.0
- summary evidence coverage 1.0
- conflict preservation 1.0

50-case set:

- finding-type precision 1.0, recall 0.9722, F1 0.9859 (Phase 4.6 was 1.0 / 0.9167 / 0.9565)
- supported-claim precision 1.0, recall 1.0, F1 1.0
- supported expected claims 35, produced 35
- correctly omitted unsupported claims 14
- agent-not-selected omissions 1
- evidence support rate 1.0
- unsupported final claim rate 0.0
- unsupported claim rate 0.1
- finding-type preservation 1.0
- citation lineage 1.0
- summary evidence coverage 1.0
- conflict preservation 1.0

Planner fixture checks are unchanged: required-agent recall 1.0, forbidden-agent precision 1.0, objective unnecessary-agent rate 0.0. Legacy unnecessary-agent rate remains 0.2000 on 24 cases and 0.1818 on 50.

Retrieval date-filter, entity-filter, and event-filter accuracy remain 1.0. Recall@5 remains 0.9375. These are lexical fixture scores.

## Known limitations

"AMD Instinct Meta data centers" still produces no finding. Finding-type recall stays below the Phase 4.5 value of 1.0 for that reason.

Document citations are hashes of document and chunk ids. `claim_evidence` rows are written when a ref is an evidence UUID. A quoted excerpt is supported through its citation, not through a new evidence row.

## Deferred

Phase 4.8 is not started. Load testing stays deferred. The async worker and Kafka topics were not redesigned.
