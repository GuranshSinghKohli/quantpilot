# Environment Variables

All secrets belong in `.env` / `.env.local` files — never commit them to Git.

## Backend (Railway / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `OPENAI_API_KEY` | **Required** | Powers all LangGraph agents and ChromaDB embeddings (`text-embedding-3-small`) | `sk-proj-...` |
| `SEC_EDGAR_USER_AGENT` | **Required** | SEC EDGAR API requires a descriptive User-Agent with contact info | `QuantPilot/1.0 (you@email.com)` |
| `ALLOWED_ORIGINS` | **Required** (production) | Comma-separated CORS origins allowed to call the API | `https://quantpilot.vercel.app,http://localhost:3000` |
| `QUANTPILOT_ROOT` | Optional | Override monorepo root path if MCP server is not auto-detected | `/app` |
| `PORT` | Optional | Injected by Railway; used by uvicorn | `8000` |

Copy `backend/.env.example` to `backend/.env` for local development.

## Frontend (Vercel / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `NEXT_PUBLIC_API_URL` | **Required** | Full URL of the deployed FastAPI backend (no trailing slash) | `https://quantpilot-backend.up.railway.app` |

Copy `frontend/.env.local.example` to `frontend/.env.local` for local development.

## Notes

- After deploying to Vercel, add your Vercel URL to backend `ALLOWED_ORIGINS` on Railway and redeploy the backend.
- `NEXT_PUBLIC_*` variables are embedded in the client bundle at build time — redeploy Vercel after changing them.
