# CreditFlow — Deployment Guide

## Architecture Overview

```
Cloudflare Pages (frontend) ──→ Render (FastAPI backend) ──→ Cloudflare Workers AI (GenAI explain)
```

| Component | Platform | URL pattern |
|-----------|----------|-------------|
| Frontend (React/Vite SPA) | Cloudflare Pages | `https://creditflow-<branch>.pages.dev` |
| Backend (FastAPI + model) | Render Free Tier | `https://creditflow-api.onrender.com` |
| LLM explanations | Cloudflare Workers AI | REST API (called by backend) |

## Step 1: Deploy Backend to Render

1. Push this repo to GitHub/GitLab.
2. Go to https://dashboard.render.com → **New +** → **Web Service**.
3. Connect your repo and select the `render.yaml` at the root (auto-detected).
4. Verify settings:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn backend.app:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/health`
   - **Plan**: Free
5. Add env vars in the Render dashboard:
   - `PYTHON_VERSION` = `3.12.7`
   - `MLFLOW_DISABLE_AGENT_HINT` = `1`
   - `CREDITFLOW_LLM_PROVIDER` = `cloudflare`
   - `CLOUDFLARE_MODEL` = `@cf/meta/llama-3.2-1b-instruct`
   - `CLOUDFLARE_ACCOUNT_ID` = *(your Cloudflare account ID — sync:false)*
   - `CLOUDFLARE_API_TOKEN` = *(your Cloudflare API token — sync:false)*
6. Click **Create Web Service** and wait for the first deploy.

### Verify Backend

```bash
curl https://creditflow-api.onrender.com/health
# {"status":"ok","model_loaded":true,"model_version":"...","model_name":"..."}

curl -X POST https://creditflow-api.onrender.com/predict \
  -H "Content-Type: application/json" \
  -d '{"income":2500,"age":32,"employment_years":4,"loan_amount":12000,"loan_term":36,"existing_debt":3500,"credit_history":5,"previous_defaults":0}'
# {"risk_probability":...,"decision":"APPROVE","model_version":"...","reasons":[...]}
```

## Step 2: Deploy Frontend to Cloudflare Pages

1. Go to https://dash.cloudflare.com → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
2. Select your repo and branch.
3. **Build settings**:
   - **Framework preset**: Vite
   - **Build command**: `npm run build` (or `cd frontend && npm install && npm run build`)
   - **Build output directory**: `frontend/dist`
   - **Root directory**: leave blank (repo root)
4. **Environment variables** (add in the Pages dashboard):
   - `VITE_API_BASE` = `https://creditflow-api.onrender.com`
5. Click **Save and Deploy**.

### SPA Routing

The file `frontend/_redirects` (committed to the repo) ensures all routes serve `index.html`:

```
/*    /index.html   200
```

Cloudflare Pages copies `_redirects` to the build output automatically.

### Verify Frontend

```bash
curl https://creditflow-<branch>.pages.dev/health
# Should proxy to backend /health (200 OK)
```

Open the Pages URL in a browser and test the Predict / Model / Monitoring tabs.

## Step 3: Cloudflare Workers AI (GenAI Explain Layer)

The backend calls Cloudflare Workers AI to generate human-readable explanations for credit decisions.

### Setup

1. Get your **Account ID** from https://dash.cloudflare.com/profile
2. Create an **API Token** at https://dash.cloudflare.com/profile/api-tokens
   - Permissions needed: `Account - Cloudflare Workers AI: Edit`
3. Set the following env vars on Render (Step 1, item 5):
   - `CLOUDFLARE_ACCOUNT_ID`
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_MODEL` = `@cf/meta/llama-3.2-1b-instruct` (free tier default)

### Verify

```bash
curl https://creditflow-api.onrender.com/llm/info
# {"provider":"cloudflare","model":"@cf/meta/llama-3.2-1b-instruct","prompt_version":"...","configured":true}
```

If `configured` is `false`, check the Render env vars are set correctly.

## Environment Variables Reference

### Render (Backend)

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `PYTHON_VERSION` | Yes | `3.12.7` | Set in dashboard |
| `MLFLOW_DISABLE_AGENT_HINT` | Yes | `1` | Silence MLflow telemetry |
| `CREDITFLOW_LLM_PROVIDER` | Yes | `cloudflare` | LLM provider selector |
| `CLOUDFLARE_ACCOUNT_ID` | For GenAI | — | **Never commit** — set in dashboard |
| `CLOUDFLARE_API_TOKEN` | For GenAI | — | **Never commit** — set in dashboard |
| `CLOUDFLARE_MODEL` | No | `@cf/meta/llama-3.2-1b-instruct` | Free-tier friendly |

### Cloudflare Pages (Frontend)

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `VITE_API_BASE` | Yes | — | Set in Pages dashboard; local fallback is `/api` |

## Security Notes

- **Never commit real secrets** to the repo. Use the Render/Pages dashboards or a `.env` file (gitignored).
- `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are marked `sync: false` in `render.yaml` so Render stores them as secrets.
- `.env` is gitignored. Copy `.env.example` to `.env` for local development.
- The `docker-compose.yml` loads from `.env.example` via `env_file` — update that file with your local placeholders if needed.

## Local Docker (Full Stack)

```bash
# 1. Copy and fill local secrets
cp .env.example .env
# Edit .env with your local CLOUDFLARE_* values

# 2. Start the stack
docker compose up --build -d

# 3. Verify
curl http://localhost:8081/health        # backend
curl http://localhost:8080/              # frontend (nginx)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Backend returns 502 on Render | Check logs in Render dashboard; ensure `requirements.txt` installs cleanly |
| CORS errors in browser | Backend CORS now allows `*` — clear browser cache and retry |
| Frontend 404 on refresh | Ensure `frontend/_redirects` is committed and Cloudflare Pages picked it up |
| LLM explanations return template | Verify `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in Render dashboard |
| Frontend can't reach API | Confirm `VITE_API_BASE` in Pages dashboard matches your Render URL |
