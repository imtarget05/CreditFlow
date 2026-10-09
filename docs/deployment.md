# CreditFlow — Deployment Guide

## Architecture Overview

```
Cloudflare Pages (frontend) ──→ Azure Container Apps (FastAPI backend, canonical) ──→ Cloudflare Workers AI (GenAI explain)
        │                                        ▲
        │                                        └── mirror: Render (render.yaml: API + managed Postgres)
(+ GHCR Docker images: api + web, auto-push từ cd.yml)
```

| Component | Platform | URL |
|-----------|----------|-----|
| Frontend (React/Vite SPA) | Cloudflare Pages | `https://creditflow.pages.dev/` |
| Backend (FastAPI + model) | Azure Container Apps | `https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io` |
| Backend mirror + Postgres | Render | `https://creditflow-api-9z1v.onrender.com` |
| LLM explanations | Cloudflare Workers AI | REST API (called by backend, NOT frontend) |
| Docker images | GHCR | `ghcr.io/<owner>/creditflow/creditflow-{api,web}` |

> Lịch sử: GH Pages `imtarget05.github.io/CreditFlow` đã thay bằng Cloudflare Pages `creditflow.pages.dev`; CF Pages cũ `creditflow-4nu.pages.dev` đã decommission (403) — chỉ giữ tham chiếu.

## Step 1: Deploy Backend to Azure Container Apps

1. Push this repo to GitHub.
2. Configure the `AZURE_CREDENTIALS` GitHub Actions secret and `CREDITFLOW_API_KEY` secret.
3. Run `.github/workflows/deploy-azure.yml` manually or push a backend change to `main`.
   The workflow deploys the API, sets the key as an Azure Container Apps secret
   (never a plain-text env var), enables production fail-closed auth, and runs a
   smoke test against the canonical endpoint.
4. Set Cloudflare Workers AI credentials in GitHub Actions secrets if the GenAI
   explanation provider is enabled.

### Verify Backend

```bash
curl https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io/health/live
# {"status":"ok","model_loaded":true,"model_version":"...","model_name":"..."}

curl -X POST https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io/predict \
  -H "Content-Type: application/json" \
  -d '{"income":8000000,"age":32,"employment_years":4,"loan_amount":12000000,"loan_term":36,"existing_debt":3500000,"credit_history":5,"previous_defaults":0}'
# 200 + {"risk_probability":...,"decision":"APPROVE","model_version":"...","reasons":[...]}

curl https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io/model/info
# {"model_version":"...","model_name":"..."}
```

## Step 2: Deploy Frontend to Cloudflare Pages

1. Repo Secrets: `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` (đã set cho `cd.yml`).
2. Push `main` → workflow `.github/workflows/cd.yml` job `deploy-frontend-cloudflare`:
   - `npm ci` + `npm run build` (trong `frontend/`)
   - `cloudflare/wrangler-action@v4` → `pages deploy frontend/dist --project-name creditflow`
3. `frontend/vite.config.js` dùng `base: process.env.VITE_BASE || "./"` — base tương đối, hoạt động ở root của Pages (không cần sub-path `/CreditFlow/`).
4. The workflow bakes the canonical Azure Container Apps URL into `VITE_API_BASE`.
5. SPA fallback: `frontend/public/_redirects` (`/* /index.html 200`) được Vite copy vào `dist/` — Cloudflare Pages tự serve index cho mọi deep path; `404.html` vẫn được copy kèm làm backup.

### Verify Frontend

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://creditflow.pages.dev/
# 200
```

Open `https://creditflow.pages.dev/` in a browser and test the Predict / Model / Monitoring tabs.
Predict E2E qua Azure backend trả 200 với ideal-profile payload.

## Step 3: Cloudflare Workers AI (GenAI Explain Layer)

The backend calls Cloudflare Workers AI to generate human-readable explanations for credit decisions.
Workers AI chỉ phục vụ GenAI explain — không còn liên quan tới frontend hosting.

### Setup

1. Get your **Account ID** from https://dash.cloudflare.com/profile
2. Create an **API Token** at https://dash.cloudflare.com/profile/api-tokens
   - Permissions needed: `Account - Cloudflare Workers AI: Edit`
3. Set the following env vars in Azure Container Apps (via Key Vault/ACA secrets, never in repo):
   - `CLOUDFLARE_ACCOUNT_ID`
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_MODEL` = `@cf/meta/llama-3.2-1b-instruct` (free tier default)

### Verify

```bash
curl https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io/llm/info
# {"provider":"cloudflare","model":"@cf/meta/llama-3.2-1b-instruct","prompt_version":"...","configured":true}
```

If `configured` is `false`, check the Azure Container Apps configuration.

## Step 4: Render Mirror (API + Managed Postgres)

`render.yaml` khai báo mirror plane đầy đủ bên cạnh Azure (canonical):

1. Service web `creditflow-api` — Docker build từ `backend/Dockerfile` (context repo root),
   `healthCheckPath: /health/live`, `autoDeploy: true` (push `main` là redeploy).
2. Managed Postgres `creditflow-db` — `DATABASE_URL` được wire tự động
   (`fromDatabase: connectionString`):
   - `pipeline/storage/ledger.py` → psycopg (ledger rows bền qua restart),
   - `pipeline/agent/checkpointer.py` → `langgraph-checkpoint-postgres` (PostgresSaver —
     paused HITL workflows sống sót qua restart/replica).
3. `CREDITFLOW_API_KEY` dùng `generateValue: true` (instance tự chứa, khác secret ACA —
   smoke script nhắm ACA là canonical).
4. LLM gateway Workers-AI là opt-in: set `CLOUDFLARE_*` + `CREDITFLOW_LLM_PROVIDER=cloudflare`
   trong dashboard Render (các key này để `sync: false`, không nằm trong repo).

### Verify Render

```bash
curl https://creditflow-api-9z1v.onrender.com/health/live
# {"status":"ok", ...}
```

Service đã linked sẵn với repo trong Render dashboard (blueprint sync trên mỗi push).

## Environment Variables Reference

### Azure Container Apps (Backend)

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `CREDITFLOW_API_KEY` | Yes | — | Stored as ACA secret `creditflow-api-key`; container references it using `secretref:creditflow-api-key`. |
| `CREDITFLOW_CORS_ORIGINS` | Yes | `https://creditflow.pages.dev,https://imtarget05.github.io` | Explicit comma-separated origin allow-list (set by `deploy-azure.sh`; GitHub Pages kept during transition). |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | For GenAI | — | **Never commit** — passed to ACA secret references by the Azure deploy workflow. |

### Cloudflare Pages (Frontend)

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `VITE_API_BASE` | Yes | Azure Container Apps URL | Baked at build time by `cd.yml`; no secret is passed to the Pages build. |

The Pages build must not receive `CREDITFLOW_API_KEY` or any other shared API
credential. Browser-delivered Vite variables are public, even when sourced from
GitHub Actions secrets. Consequently, the protected ledger and approval calls
remain unavailable from the static frontend and return 401 by design. A
user-authenticated server-side proxy is a separate future requirement; do not
reintroduce a shared key into the frontend bundle.

## Security Notes

- **Never commit real secrets** to the repo. Use Azure Container Apps/Key Vault or a `.env` file (gitignored).
- **Never expose `CREDITFLOW_API_KEY` in the Pages build or browser bundle.** The old committed frontend value must be treated as compromised and rotated in the backend/hosting dashboards if it matches any active credential; repo history is not rewritten by this remediation.
- `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are stored as Azure Container Apps secrets; GitHub Actions passes them only to the Azure deploy workflow.
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

Docker images `creditflow-api` / `creditflow-web` cũng được auto-push lên GHCR (`ghcr.io/<owner>/creditflow/...`) từ `cd.yml` jobs `build-api-image` / `build-web-image`.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Frontend 404 on refresh (unknown deep path) | Đã xử lý: `frontend/public/404.html` (redirect về `/CreditFlow/`) + bước copy trong `cd.yml` (`cp public/404.html dist/404.html`) đảm bảo artifact có 404.html. Đây là giới hạn đã biết của GitHub Pages: unknown deep path trả HTTP 404 **cùng nội dung trang redirect** (browser có JS tự chuyển về app) — app là single-view không router nên không mất trạng thái route. `_redirects` là file Cloudflare-Pages-only, GitHub Pages bỏ qua — giữ lại chỉ để tham chiếu |
| Frontend can't reach API | Confirm the Pages build uses the canonical Azure URL and that ACA ingress allows HTTPS traffic. |
| Backend unavailable | Check Azure Container Apps revision and `/health/ready` logs/status. |
| CORS errors in browser | Confirm `CREDITFLOW_CORS_ORIGINS=https://imtarget05.github.io` in the ACA configuration. Never use `*` — the API refuses to start with it. |
| LLM explanations return template | Verify Cloudflare secrets in ACA and re-check `/llm/info` (`configured` phải `true`). |
