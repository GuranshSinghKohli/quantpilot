# ResearchResult (Phase 4.2)

`POST /api/research` returns a bounded, serializable `ResearchResult`. It is not a prose report.

```
request
findings                  # supported / uncertain / conflicting only
verified_claims           # supported + uncertain (partial)
unsupported_claims
conflicts
citations                 # only citations used by claims
agent_metadata            # model, prompt_version, tokens, retries, status
verification_summary
errors
unavailable_components    # semantic | llm | agent name
created_at
research_job_id           # when persisted
```

## Persistence

Optional write to existing tables (`0003`): `research_jobs`, `agent_runs`, `research_claims`. `claim_evidence` is created only when an evidence_ref is a real `evidence_records.id`. Full EvidencePackages and raw LLM transcripts are not stored.

Job type is `event_research` when the request has event IDs and no entity IDs; otherwise `company_research`.

## Limits

Request ceilings are the Phase 4.1 `RETRIEVAL_MAX_*` settings. Oversized requests are HTTP 400. Findings per agent are capped by the LLM payload (`max_length=20`) and specialist clipping (12).

## Hallucination guard

Before a finding is `supported`:

1. citation_id exists in the package
2. FACT/OBSERVATION have ≥1 valid citation
3. relationship FACT requires a graph_fact, a matching event_fact, or a document excerpt with an explicit relationship verb
4. co-occurrence without that verb is `unsupported`
5. contradictions become `conflicting` (both kept)
6. buy/sell/hold/price-target language is `unsupported`
7. market causal language is `unsupported`

The system prefers "insufficient evidence" over a fabricated explanation.
