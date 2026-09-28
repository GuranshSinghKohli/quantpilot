# Agent tools (Phase 4.2)

Tools wrap existing retrieval services. They do not contain SQL or Cypher and do not accept raw queries.

| Tool | Service | Provenance |
|---|---|---|
| `retrieve_context` | `assemble_package` | package citations |
| `get_entity` | `load_entities` | `source.record` required |
| `get_entity_relationships` | `load_graph_facts` | `source_id` + `evidence_id` |
| `get_event` | `load_events` | `source_id` or `evidence_id` |
| `get_market_impact` | `load_market_facts` | `event_id` + `calculation_version` |
| `retrieve_documents` | `search` | document/chunk/source IDs |
| `verify_evidence` | citation lookup in the current package | citation_id must exist |
| `retrieve_cited_evidence` | citation + linked documents | same |

A factual payload without provenance is `missing_provenance` and is not research-usable.

## Permissions

| Agent | Allowed |
|---|---|
| news | retrieve_documents, get_event, verify_evidence |
| company | get_entity, get_entity_relationships, get_event, retrieve_documents |
| technology | get_entity, get_entity_relationships, get_event, retrieve_documents |
| market | get_entity, get_event, get_market_impact, verify_evidence |
| evidence | verify_evidence, retrieve_cited_evidence |
| graph node | retrieve_context |

Denied tools return `tool_not_permitted:<name>`.

`ToolResult`: `data`, `citations`, `source_ids`, `evidence_ids`, `errors`.
