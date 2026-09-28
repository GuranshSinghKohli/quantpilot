# Agent architecture (Phase 4.3)

Specialized research agents consume a Phase 4.1 **EvidencePackage**. They do not search PostgreSQL, Neo4j, pgvector, Kafka, or S3 themselves.

This path is **not** the investigation graph and **not** the ticker analysis graph. Those remain in `app/workflows/`.

The master node is bounded. It chooses which of the four specialists to run and how verified findings are grouped. It does not create tools, nodes, evidence, or citations. See `docs/PHASE4_3_PLAN.md`.

## Agents

| Agent | Input | Output | Tools |
|---|---|---|---|
| NewsAgent | EvidencePackage | `AgentFinding[]` | retrieve_documents, get_event, verify_evidence |
| CompanyAgent | EvidencePackage | `AgentFinding[]` | get_entity, get_entity_relationships, get_event, retrieve_documents |
| TechnologyAgent | EvidencePackage | `AgentFinding[]` | get_entity, get_entity_relationships, get_event, retrieve_documents |
| MarketAgent | EvidencePackage | `AgentFinding[]` | get_entity, get_event, get_market_impact, verify_evidence |
| EvidenceAgent | all findings + package | `VerifiedClaim[]` | verify_evidence, retrieve_cited_evidence |

`retrieve_context` is used only by the graph node that builds the package.

## Finding types

| Type | Meaning | Can be `supported`? |
|---|---|---|
| FACT | Directly stated by a source or structured record | Yes, if citations pass the guard |
| OBSERVATION | Measured/structured value | Yes |
| INFERENCE | Interpretation of supported facts | No — `uncertain` |
| SCENARIO | Conditional possibility | No — `uncertain` |

## Confidence vs verification

`confidence` is the agent's self-score. `verification_status` is assigned by the deterministic guard. A high confidence unsupported claim stays `unsupported`.

Stored values reuse `VerificationStatus`: `supported` (spec VERIFIED), `uncertain` (PARTIALLY_SUPPORTED), `unsupported`, `conflicting`.

## Security

Agents cannot execute SQL or Cypher, read env/credentials, touch the filesystem or shell, or call arbitrary URLs. Only the tool table above exists.

## Failure

One specialist can fail without dropping the others. EvidenceAgent still runs. LLM down → `POST /api/research` reports `llm_unavailable`. `/health` does not depend on the LLM.
