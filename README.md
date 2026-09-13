# CreditFlow

**ML Risk Decision Support System** — credit/loan risk evaluation with 4 classical ML models, human-in-the-loop approval workflow, and an auditable core-banking ledger.

[![CI](https://github.com/imtarget05/CreditFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/imtarget05/CreditFlow/actions/workflows/ci.yml)

> **Data transparency note.** The data under `data/` is synthetic — it exercises the real pipeline end-to-end. Not a claim of serving real bank customers.

---

## At a glance

| Aspect | Details |
|---|---|
| **Stack** | Python/FastAPI · React/Vite · scikit-learn · XGBoost · LangGraph · LangChain · SQLite |
| **ML models** | Logistic Regression · Decision Tree · Random Forest · XGBoost |
| **Key endpoints** | `/health` · `/predict` · `/model/info` · `/metrics` · `/drift` · `/predict/graph` (approval workflow) |
| **Deployment** | Docker (GHCR) · Render (backend) · GitHub Pages (frontend) |

---

## What it does

Evaluates credit/loan applications from tabular data through a full pipeline:

```
Application → Validate → Clean → EDA → Feature Engineering
→ Train 4 models → Evaluate (business cost matrix) → Register best → Serve via FastAPI
→ React UI → Monitoring
```

Risk decisions use a **business cost matrix** where a missed default (FN, cost 5) outweighs a false rejection (FP, cost 1) — so the model and threshold are tuned for recall, not raw accuracy.

**Borderline applications** go through a LangGraph decision workflow with a **human-approval interrupt**. Approved loans are recorded in a **SQLite core-banking ledger** with contract codes and SHA-256 tamper-evident hashes.

---

## API overview

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service + model status |
| `GET /model/info` | Production model + metrics |
| `POST /predict` | Real ML prediction (returns risk score + decision) |
| `GET /metrics` | Runtime counters + benchmark metrics |
| `GET /drift` | PSI-based data/prediction drift vs training reference |
| `POST /predict/graph` | Start LangGraph decision workflow |
| `POST /predict/graph/{id}/approve` | Human approval → resume + disburse |
| `GET /predict/graph/{id}` | Workflow state |
| `GET /audit/{application_id}` | Audit trail |
| `GET /api/applications` | Loan application ledger |
| `GET /api/disbursements` | Disbursement ledger |

---

## Quick start

### Local dev

```bash
# Backend (repo root)
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8080

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # → http://localhost:5173
```

Backend runs on `:8080`, frontend proxies `/api` to it. Open `http://localhost:5173`.

### Train models

```bash
pip install -r requirements.txt mlflow==3.16.0
MLFLOW_TRACKING_URI=sqlite:///mlflow.db python scripts/train_models.py
```

Produces `models/production/{pipeline.joblib, meta.json, benchmark_results.csv}`.

### Docker Compose (local full stack)

```bash
docker compose up --build -d
```

- API: `localhost:8081` (container port 8080)
- Web: `localhost:8080` (nginx + nginx proxy `/api` → api:8080)

---

## ML monitoring

- **Runtime:** `GET /metrics` — in-process request/latency/error counters + avg predict latency (demo scope; counters reset on restart, no Prometheus/exporter)
- **Drift:** `GET /drift` — PSI-based feature + prediction drift (NO_DRIFT / DRIFT_DETECTED / INSUFFICIENT_DATA)
- **MLflow:** Experiment `creditflow-risk`, registered model `creditflow-risk`. View with `mlflow ui`

---

## Repository layout

```
backend/          FastAPI app + prediction service
frontend/         React + Vite UI
pipeline/         Training pipeline, feature engineering, agent (LangGraph + LangChain)
scripts/          train_models.py, utilities
models/production/ Trained pipeline + metadata + benchmarks
data/             Synthetic dataset + SQLite ledger + checkpoints
tests/            Unit + integration tests
deploy/           Docker + Render config
docs/             Spec, QA acceptance, architecture
```

---

## Status

- ✅ Demoable locally end-to-end (UI → API → real model)
- ✅ 4 ML models trained + benchmarked + registered
- ✅ LangGraph approval workflow with persisted state
- ✅ SQLite ledger with tamper-evident disbursement records
- ✅ Drift monitoring (PSI) + runtime metrics
- ✅ LLM explain layer (Cloudflare Workers AI, falls back to template offline)
- ✅ CI: GitHub Actions (test + build) + CD (GHCR images + Pages deploy)
- ✅ Docker images: `ghcr.io/imtarget05/creditflow/creditflow-api` + `ghcr.io/imtarget05/creditflow/creditflow-web`

---

## CI / CD

### Pipeline

```
push to main
  ├─ test (pytest unit + API + LangGraph) — ubuntu-latest, Python 3.12
  ├─ build-frontend (npm ci + vite build) — ubuntu-latest, Node 22
  ├─ build-api-image (docker build + push to GHCR) — needs: test
  ├─ build-web-image (docker build + push to GHCR) — needs: build-frontend
  ├─ deploy-frontend-pages (GitHub Pages) — needs: build-frontend
  └─ notify-render (optional webhook) — needs: build-api-image, deploy-frontend-pages
```

### Quay mình

| Component | Registry / Platform | Image / URL | Trigger |
|---|---|---|---|
| **Backend Docker** | GHCR | `ghcr.io/imtarget05/creditflow/creditflow-api:latest` (+ git SHA tag) | push to main |
| **Frontend Docker** | GHCR | `ghcr.io/imtarget05/creditflow/creditflow-web:latest` (+ git SHA tag) | push to main |
| **Frontend static** | GitHub Pages | `https://imtarget05.github.io/CreditFlow/` | push to main |
| **Backend service** | Render | setup in Render dashboard | manual (native GitHub integration or webhook) |

### Tags

- `latest` — head of `main`
- `{short-sha}` — mỗi commit có 1 tag, dễ rollback

### Cache

Docker Buildx sử dụng GitHub Actions cache (GHA) để tăng tốc build.

### Permissions

| Scope | Use |
|---|---|
| `contents: read` | checkout repo |
| `packages: write` | push Docker images to GHCR |
| `pages: write` + `id-token: write` | deploy GitHub Pages |

---

## Kích hoạt CD

### GitHub Pages (frontend static)

1. Repo → **Settings** → **Pages** → Source: **GitHub Actions**
2. Push to `main` — job `deploy-frontend-pages` tự chạy

### GHCR (Docker images)

Tự động — không cần setup thêm. Images public nếu repo public, private nếu repo private.

Xem images:
- `ghcr.io/imtarget05/creditflow/creditflow-api:latest`
- `ghcr.io/imtarget05/creditflow/creditflow-web:latest`

### Render (backend service)

Hai cách:

**Cách 1 — Native GitHub integration (khuyên dùng):**

1. Render dashboard → New Web Service → connect repo `imtarget05/CreditFlow`
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `uvicorn backend.app:app --host 0.0.0.0 --port $PORT`
4. Render tự động redeploy khi push đến `main`

**Cách 2 — Webhook từ GitHub Actions (nếu muốn control chính xác):**

1. Render dashboard → Settings → Webhooks → add webhook
2. Repo → Settings → Secrets → add `RENDER_DEPLOY_HOOK` = webhook URL
3. Job `notify-render` sẽ curl webhook sau khi build API image thành công

---

## Kết nối frontend ↔ backend

Frontend đọc env `VITE_API_BASE` để biết backend ở đâu.

| Môi trường | VITE_API_BASE | Ghi chú |
|---|---|---|
| Local dev (Vite proxy) | `/api` (mặc định) | Vite proxy → `localhost:8080` |
| Docker Compose | `/api` (mặc định) | Nginx proxy → `api:8080` |
| GitHub Pages + Render backend | `https://creditflow-api-ko2h.onrender.com` | Baked at build time (`.env.production`), build lại nếu đổi URL |
| GitHub Pages + backend khác | `https://other-backend-url` | Set `VITE_API_BASE` khi build |

Build frontend cho production:

```bash
cd frontend
VITE_API_BASE=https://creditflow-api-ko2h.onrender.com npm run build
```

---

## Docker images usage

### Chạy từ GHCR

```bash
# API
docker run -d --name creditflow-api \
  -p 8080:8080 \
  -e CREDITFLOW_LLM_PROVIDER=cloudflare \
  -e CLOUDFLARE_ACCOUNT_ID=xxx \
  -e CLOUDFLARE_API_TOKEN=yyy \
  -e CLOUDFLARE_MODEL=@cf/meta/llama-3.2-1b-instruct \
  ghcr.io/imtarget05/creditflow/creditflow-api:latest

# Web (nginx)
docker run -d --name creditflow-web -p 8080:8080 ghcr.io/imtarget05/creditflow/creditflow-web:latest
```

### Docker Compose (local)

```bash
docker compose up --build -d
```

---

## Secrets

| Secret | Nơi lưu | Ghi chú |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Render dashboard / `.env` (local) | sync:false trong render.yaml |
| `CLOUDFLARE_API_TOKEN` | Render dashboard / `.env` (local) | sync:false trong render.yaml |
| `GITHUB_TOKEN` | GitHub Actions (auto) | dành cho GHCR push, không cần setup |
| `RENDER_DEPLOY_HOOK` | GitHub repo secrets (optional) | nếu dùng cách 2 webhook |

---

## Dockerfile

| File | Build context | Image | Ghi chú |
|---|---|---|---|
| `backend/Dockerfile` | repo root (`.`) | `creditflow-api` | COPY backend/ + pipeline/ + models/ |
| `frontend/Dockerfile` | `frontend/` | `creditflow-web` | multi-stage: node build → nginx serve |

Docker Compose `docker-compose.yml` là canonical cho local dev full-stack.
---

## Documentation

- [`docs/spec.md`](docs/spec.md) — full product specification
- [`docs/qa/manual-acceptance.md`](docs/qa/manual-acceptance.md) — manual acceptance test evidence
