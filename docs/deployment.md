# CreditFlow — Deployment Guide

## Architecture Overview

```
GitHub Pages (frontend) ──→ Render (FastAPI backend) ──→ Cloudflare Workers AI (GenAI explain)
(+ GHCR Docker images: api + web, auto-push từ cd.yml)
```

| Component | Platform | URL |
|-----------|----------|-----|
| Frontend (React/Vite SPA) | GitHub Pages | `https://imtarget05.github.io/CreditFlow/` |
| Backend (FastAPI + model) | Render Free Tier | `https://creditflow-api-ko2h.onrender.com` |
| LLM explanations | Cloudflare Workers AI | REST API (called by backend, NOT Pages) |
| Docker images | GHCR | `ghcr.io/<owner>/CreditFlow/creditflow-{api,web}` |

> Lịch sử: Cloudflare Pages cũ `creditflow-4nu.pages.dev` đã decommission (trả 403) — không còn trong stack, chỉ giữ 1 dòng này làm tham chiếu.

## Step 1: Deploy Backend to Render

1. Push this repo to GitHub/GitLab.
2. Go to https://dashboard.render.com → **New +** → **Web Service**.
3. Connect your repo and select the `render.yaml` at the root (auto-detected).
4. Verify settings (giữ nguyên `render.yaml`):
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn backend.app:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/health`
   - **Plan**: Free
   - **Python**: `PYTHON_VERSION` = `3.12.7`
5. Add env vars in the Render dashboard:
   - `PYTHON_VERSION` = `3.12.7`
   - `MLFLOW_DISABLE_AGENT_HINT` = `1`
   - `CREDITFLOW_LLM_PROVIDER` = `cloudflare`
   - `CLOUDFLARE_MODEL` = `@cf/meta/llama-3.2-1b-instruct`
   - `CLOUDFLARE_ACCOUNT_ID` = *(your Cloudflare account ID — sync:false, dashboard only)*
   - `CLOUDFLARE_API_TOKEN` = *(your Cloudflare API token — sync:false, dashboard only)*
6. Click **Create Web Service** and wait for the first deploy.

> Note: Render free-tier có cold-start ~30s ở request đầu sau sleep — đây là hành vi bình thường, không retry ngầm.

### Verify Backend

```bash
curl https://creditflow-api-ko2h.onrender.com/health
# {"status":"ok","model_loaded":true,"model_version":"...","model_name":"..."}

curl -X POST https://creditflow-api-ko2h.onrender.com/predict \
  -H "Content-Type: application/json" \
  -d '{"income":8000000,"age":32,"employment_years":4,"loan_amount":12000000,"loan_term":36,"existing_debt":3500000,"credit_history":5,"previous_defaults":0}'
# 200 + {"risk_probability":...,"decision":"APPROVE","model_version":"...","reasons":[...]}

curl https://creditflow-api-ko2h.onrender.com/model/info
# {"model_version":"...","model_name":"..."}
```

## Step 2: Deploy Frontend to GitHub Pages

1. Repo **Settings → Pages → Source: GitHub Actions**.
2. Push `main` → workflow `.github/workflows/cd.yml` job `deploy-frontend-pages`:
   - `npm ci` + `npm run build` (trong `frontend/`)
   - Upload `frontend/dist` via `actions/upload-pages-artifact@v3`
   - Deploy via `actions/deploy-pages@v4` (branch `gh-pages`)
3. `frontend/vite.config.js` giữ `base: '/CreditFlow/'` để routing/assets đúng sub-path.
4. `VITE_API_BASE=https://creditflow-api-ko2h.onrender.com` (baked at build time — build lại nếu đổi URL backend).
5. SPA fallback: `frontend/_redirects` (`/* /index.html 200`) và `frontend/public/404.html` (redirect về `/CreditFlow/`) đã commit — xử lý deep-route refresh trên GitHub Pages.

### Verify Frontend

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://imtarget05.github.io/CreditFlow/
# 200
```

Open `https://imtarget05.github.io/CreditFlow/` in a browser and test the Predict / Model / Monitoring tabs.
Predict E2E qua backend Render trả 200 APPROVE với ideal-profile payload.

## Step 3: Cloudflare Workers AI (GenAI Explain Layer)

The backend calls Cloudflare Workers AI to generate human-readable explanations for credit decisions.
Workers AI chỉ phục vụ GenAI explain — không còn liên quan tới frontend hosting.

### Setup

1. Get your **Account ID** from https://dash.cloudflare.com/profile
2. Create an **API Token** at https://dash.cloudflare.com/profile/api-tokens
   - Permissions needed: `Account - Cloudflare Workers AI: Edit`
3. Set the following env vars on Render (Step 1, item 5 — Render dashboard, never in repo):
   - `CLOUDFLARE_ACCOUNT_ID`
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_MODEL` = `@cf/meta/llama-3.2-1b-instruct` (free tier default)

### Verify

```bash
curl https://creditflow-api-ko2h.onrender.com/llm/info
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
| `CLOUDFLARE_ACCOUNT_ID` | For GenAI | — | **Never commit** — set in Render dashboard (`sync:false`) |
| `CLOUDFLARE_API_TOKEN` | For GenAI | — | **Never commit** — set in Render dashboard (`sync:false`) |
| `CLOUDFLARE_MODEL` | No | `@cf/meta/llama-3.2-1b-instruct` | Free-tier friendly |

### GitHub Pages (Frontend)

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `VITE_API_BASE` | Yes | — | Baked at build time; must point to Render URL (`https://creditflow-api-ko2h.onrender.com`); local fallback is `/api` |

## Security Notes

- **Never commit real secrets** to the repo. Use the Render dashboard or a `.env` file (gitignored).
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

Docker images `creditflow-api` / `creditflow-web` cũng được auto-push lên GHCR (`ghcr.io/<owner>/CreditFlow/...`) từ `cd.yml` jobs `build-api-image` / `build-web-image`.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Frontend 404 on refresh (SPA fallback FAIL) | Đã xử lý: `frontend/public/404.html` (redirect về `/CreditFlow/`) + bước copy trong `cd.yml` (`cp public/404.html dist/404.html`) đảm bảo artifact có 404.html. Nếu vẫn 404 sau deploy, kiểm tra artifact `frontend/dist` có `404.html` + `github-pages` environment đã enable |
| Frontend can't reach API | Confirm `VITE_API_BASE` lúc build trỏ đúng Render URL (`https://creditflow-api-ko2h.onrender.com`); build lại sau khi đổi env |
| Backend 502 / request đầu ~30s | Render free-tier cold-start sau sleep — chờ rồi retry 1 lần; nếu vẫn 502, check logs in Render dashboard, ensure `requirements.txt` installs cleanly |
| CORS errors in browser | Backend CORS now allows `*` — clear browser cache and retry |
| LLM explanations return template | Verify `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in Render dashboard, then re-check `/llm/info` (`configured` phải `true`) |
