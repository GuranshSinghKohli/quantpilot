# Deployment Guide

Deploy the **backend** to [Railway](https://railway.app) and the **frontend** to [Vercel](https://vercel.com). See [ENV_VARIABLES.md](./ENV_VARIABLES.md) for every variable.

> **You must deploy both services** and connect them via `NEXT_PUBLIC_API_URL` + `ALLOWED_ORIGINS`. Live deployment requires your GitHub repo and platform accounts — this guide is step-by-step for you to run.

---

## Prerequisites

1. Code pushed to a **public or private GitHub repository**
2. [Railway](https://railway.app) account
3. [Vercel](https://vercel.com) account
4. `OPENAI_API_KEY` and `SEC_EDGAR_USER_AGENT` ready

---

## Part 1 — Backend (Railway)

### 1. Push code to GitHub

```bash
git add .
git commit -m "Phase 7: deployment and documentation"
git push origin main
```

### 2. Create Railway project

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select your `quantpilot` repository
3. Add a new service for the backend

### 3. Configure root directory

1. Open the service → **Settings** → **Root Directory**
2. Set to: `backend`
3. Railway uses Nixpacks and detects Python from `requirements.txt`

The **full monorepo is cloned** even when root is `backend/`, so `mcp_server/` at the repo root remains available to the MCP stdio client.

### 4. Environment variables

In **Variables**, add:

| Variable | Value |
|----------|--------|
| `OPENAI_API_KEY` | Your OpenAI key |
| `SEC_EDGAR_USER_AGENT` | `QuantPilot/1.0 (your-email@example.com)` |
| `ALLOWED_ORIGINS` | `http://localhost:3000` (add Vercel URL after frontend deploy) |

### 5. Start command

Railway reads `backend/railway.toml` and `backend/Procfile`:

```toml
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
```

No manual override needed unless you prefer the dashboard.

### 6. Deploy and copy URL

1. Click **Deploy** and wait for a green build
2. **Settings** → **Networking** → **Generate Domain**
3. Copy the public URL, e.g. `https://quantpilot-backend-production.up.railway.app`
4. Verify: `curl https://YOUR-RAILWAY-URL/health` → `{"status":"ok"}`

### ChromaDB on Railway

ChromaDB uses **local file storage** under `backend/data/chroma_db/`. On Railway, this **resets on redeploy** (ephemeral filesystem).

> For persistent vector memory in production, migrate to [ChromaDB Cloud](https://www.trychroma.com/) or another managed vector DB. Local storage is fine for portfolio demos and interviews.

---

## Part 2 — Frontend (Vercel)

### 1. Import project

1. [vercel.com](https://vercel.com) → **Add New** → **Project**
2. Import the same GitHub repository
3. **Root Directory**: `frontend`
4. Framework: **Next.js** (auto-detected)

### 2. Environment variable

| Name | Value |
|------|--------|
| `NEXT_PUBLIC_API_URL` | Your Railway URL from Part 1 (no trailing slash) |

Example: `https://quantpilot-backend-production.up.railway.app`

### 3. Deploy

1. Click **Deploy**
2. Copy the Vercel URL, e.g. `https://quantpilot.vercel.app`
3. Update Railway `ALLOWED_ORIGINS` to include the Vercel URL (comma-separated)
4. **Redeploy Railway** so CORS picks up the new origin

### 4. Update README live link

Replace the placeholder demo URL in `README.md` with your real Vercel URL.

---

## Part 3 — Production smoke test

On your **live Vercel URL**:

1. Open the dashboard
2. Search **AAPL**
3. Wait for the multi-agent pipeline (~30–60s)
4. Confirm: report, confidence meter, citations, watchlist/history
5. Optional: `curl https://YOUR-RAILWAY-URL/api/observability/runs`

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| CORS error in browser | Add Vercel URL to `ALLOWED_ORIGINS` on Railway, redeploy backend |
| “Backend API is unreachable” | Check `NEXT_PUBLIC_API_URL`, Railway health endpoint |
| Analysis times out | Normal for cold start; retry; Railway hobby tier may be slow |
| MCP / tool errors | Ensure repo includes `mcp_server/`; check Railway logs |
| Memory search empty after redeploy | Expected — ChromaDB resets on Railway redeploy |

---

## Local vs production checklist

- [ ] `backend/.env` with keys for local
- [ ] `frontend/.env.local` with `NEXT_PUBLIC_API_URL=http://localhost:8000`
- [ ] Railway backend deployed with all env vars
- [ ] Vercel frontend deployed with Railway URL
- [ ] `ALLOWED_ORIGINS` includes Vercel domain
- [ ] Live AAPL analysis succeeds end-to-end
