/** Single source for on-desk + README stack copy. Only list what is real. */

export const PROJECT_LINE =
  "Flagship systems project — market & technology intelligence, not a trading product.";

export const WORKFLOW =
  "Event → Entity → Relationship → Market → Research → Verification → Report";

export const CURRENT_STACK: { layer: string; tech: string }[] = [
  { layer: "Frontend", tech: "Next.js 14, TypeScript, Tailwind" },
  { layer: "API", tech: "Python, FastAPI" },
  { layer: "Agents", tech: "LangGraph + OpenAI (investigation, analysis, EvidencePackage research)" },
  { layer: "Store", tech: "PostgreSQL / SQLite (Alembic), optional Redis, ChromaDB (investigation memory), pgvector (retrieval index)" },
  { layer: "Tools", tech: "MCP, yfinance, SEC EDGAR" },
  {
    layer: "Ingest",
    tech: "Massive + SEC adapters → Kafka (single-node KRaft) → lake writer → MinIO raw lake (compose profile)",
  },
  {
    layer: "Process",
    tech: "PySpark 4.1 batch, local[*] in one container: raw JSON → validated Parquet + rejects + manifests",
  },
  {
    layer: "Load",
    tech: "Manifest-driven canonical loader (pyarrow): processed Parquet → validated, idempotent upserts into Postgres",
  },
  {
    layer: "Graph",
    tech: "Postgres entities/relationships/aliases/candidates/events/event_impacts (SoT) → optional Neo4j 5 projection + reconcile/repair (profiles graph, graph-test)",
  },
  { layer: "Ship", tech: "Docker Compose, GitHub Actions, Pytest (SQLite + Postgres tiers)" },
  {
    layer: "Observe",
    tech: "GET /metrics (Prometheus text), optional Prometheus scrape, pipeline-status/check/reconcile CLI",
  },
];

export const TARGET_STACK: { layer: string; tech: string }[] = [
  { layer: "Stream", tech: "Managed Kafka (RF ≥ 3), more stream consumers" },
  { layer: "Lake", tech: "AWS S3, Spark cluster, Airflow" },
  { layer: "Agents", tech: "Master research agent / async research jobs (not shipped)" },
  { layer: "Infra", tech: "Terraform, AWS, OTel / Grafana" },
];
