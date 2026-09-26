/** Single source for on-desk + README stack copy. Only list what is real. */

export const PROJECT_LINE =
  "Flagship systems project — market & technology intelligence, not a trading product.";

export const WORKFLOW =
  "Event → Entity → Relationship → Market → Research → Verification → Report";

export const CURRENT_STACK: { layer: string; tech: string }[] = [
  { layer: "Frontend", tech: "Next.js 14, TypeScript, Tailwind" },
  { layer: "API", tech: "Python, FastAPI" },
  { layer: "Agents", tech: "LangGraph + OpenAI" },
  { layer: "Store", tech: "PostgreSQL / SQLite (Alembic), optional Redis, ChromaDB" },
  { layer: "Tools", tech: "MCP, yfinance, SEC EDGAR" },
  { layer: "Ship", tech: "Docker Compose, GitHub Actions, Pytest (SQLite + Postgres tiers)" },
];

export const TARGET_STACK: { layer: string; tech: string }[] = [
  { layer: "Stream", tech: "Kafka + workers" },
  { layer: "Lake", tech: "S3, Parquet, Spark, Airflow" },
  { layer: "Graph", tech: "Neo4j + pgvector" },
  { layer: "Infra", tech: "Terraform, AWS, OTel / Prometheus / Grafana" },
];
