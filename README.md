# CreditFlow — ML Risk Decision Support System

[![CI](https://github.com/imtarget05/CreditFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/imtarget05/CreditFlow/actions/workflows/ci.yml)

**Production-oriented ML application** that evaluates the risk of a credit / loan
application from tabular data, trains and benchmarks 4 classical ML models, versions
the serving model, and exposes a real prediction service through FastAPI + a React UI.
Borderline applications route through a **LangGraph decision workflow with a
human-approval interrupt**, and every approved loan is recorded in a **durable
SQLite core-banking ledger** (contract + tamper-evident disbursement record) that
survives server restarts.

> ⚠️ **Proxy dataset.** The dataset under `data/` is **synthetic** (deterministic,
> seeded). It exists to exercise the real production pipeline end-to-end. It is **not**
> a claim of having served real bank customers (spec §19.2).

---

## What it solves

> Optimise detection of customers at risk of default while controlling how many good
> customers are wrongly rejected. (spec §3)

Decision engineering uses a business cost matrix where a **missed default (FN, cost 5)
costs more than a wrongly rejected good customer (FP, cost 1)**, so the model and the
probability threshold are chosen recall-centrically, not by raw accuracy.

Pipeline:

```
Loan Application → Validation → Cleaning → EDA
→ Feature Engineering → Train [Logistic|Tree|RF|XGBoost] → Evaluate
→ Select (business cost) → Register → Serve (FastAPI) → React UI → Monitoring (/metrics)
```

---

## Architecture

```
Frontend (React/Vite) :5173 ──proxy /api──► FastAPI :8080
                                              ├─ /health                            service + model status
                                              ├─ /predict                           real ML prediction
                                              ├─ /model/info                        production model + metrics
                                              ├─ /metrics                           runtime + benchmark
                                              ├─ /drift                             ML drift monitoring (PSI)
                                              ├─ /llm/info                          LLM provider status (no secrets)
                                              ├─ POST /predict/graph                start decision workflow
                                              ├─ POST /predict/graph/{id}/approve   human approval → resume + disburse
                                              ├─ GET  /predict/graph/{id}           workflow state
                                              ├─ GET  /audit/{application_id}       audit trail
                                              ├─ GET  /api/applications             loan application ledger (SQLite)
                                              └─ GET  /api/disbursements            disbursement ledger (SQLite)
FastAPI ─► Prediction Service ─► preprocessor + model (models/production/)
FastAPI ─► LangGraph decision workflow ─► interrupt at REVIEW ─► FileCheckpointSaver
                                              (data/creditflow_checkpoints.pkl — paused state survives restart)
FastAPI ─► SQLite ledger (data/creditflow_ledger.db): loan_applications + disbursements
Training: scripts/train_models.py → data/creditflow_dataset.csv
          → models/production/{pipeline.joblib, meta.json, benchmark_results.csv, reference_stats.json}
```

## Repository layout

```
backend/            FastAPI app + prediction service
frontend/           React + Vite UI
pipeline/agent/     LangGraph decision workflow (state machine, human-approval interrupt, file-backed checkpoint saver)
pipeline/storage/   SQLite core-banking ledger (loan_applications + disbursements)
pipeline/           data gen, validation, feature engineering, modeling, threshold
scripts/            train_models.py (benchmark + artifact + optional MLflow)
models/production/  trained model + meta + benchmark results (committed)
data/               synthetic proxy dataset (committed)
tests/              unit + API tests (pytest)
notebooks/          EDA + model benchmark notebooks
Dockerfile          inference service container
docker-compose.yml  local orchestration (api + optional mlflow)
render.yaml         Render web service config
docs/spec.md        product specification (source of truth)
```

---

## How to run (local)

### 1. Train the model & produce artifacts

```bash
python scripts/train_models.py
```

MLflow (experiment `creditflow-risk` + registered model `creditflow-risk`).
**Backend is SQLite** (`sqlite:///mlflow.db`), so set the env var before training:

```bash
# Terminal 1 — train (writes to SQLite DB + logs runs/registry)
MLFLOW_TRACKING_URI=sqlite:///mlflow.db python scripts/train_models.py

# Terminal 2 — MLflow UI (after at least one training run)
MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui --port 5000
# open http://localhost:5000 → experiment "creditflow-risk" (runs + metrics + artifacts)
# Models → "creditflow-risk" (registered versions)
```

Creates `models/production/pipeline.joblib`, `meta.json`, and `benchmark_results.csv`
(committed). Trains Logistic Regression, Decision Tree, Random Forest, XGBoost, chooses
the model with lowest business cost, and logs a run to MLflow if `mlflow` is installed
(gracefully skipped otherwise).

### 2. Start the API

```bash
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080
```

Open interactive docs at `http://localhost:8080/docs` (Swagger UI).

### 3. Start the UI

```bash
cd frontend && npm install && npm run dev
```

Open `http://localhost:5173`. The Predict tab submits a real profile to the live model;
the Model tab shows the production model + metrics; the Monitoring tab shows runtime
request/latency/error counters and the 4-model benchmark.

> ⚠️ The API listens on **:8080** (port 8000 may be used by the user's parallel Hermes app).

### 4. Run tests

```bash
python -m pytest tests/ -q
# 116 passed — unit + API + LangGraph workflow + ledger/disbursement E2E +
# checkpoint persistence (restart survival) + drift + deploy checks
```

---

## API

`POST /predict` (request — spec §4.1):

```json
{
  "income": 2500, "age": 32, "employment_years": 4,
  "loan_amount": 12000, "loan_term": 36, "existing_debt": 3500,
  "credit_history": 5, "previous_defaults": 0
}
```

Response (spec §4.3): `risk_probability`, `risk_level` (LOW/MEDIUM/HIGH),
`decision` (APPROVE/REVIEW/REJECT), `model_version`, `model_name`, `reasons`.

Decision buckets (configurable, `pipeline/modeling/threshold.py`):
`< 0.50 → APPROVE`, `0.50–0.80 → REVIEW`, `> 0.80 → REJECT`.

Validation: missing field / wrong type / negative value → **422** with detail
(Pydantic + business rules in `pipeline/validation/schemas.py`).

---

## Approval workflow — human-in-the-loop, restart-safe

Borderline applications (REVIEW band) do not get an automatic decision. The
LangGraph workflow pauses at a `human_approval` interrupt and waits for a
credit analyst. The full end-to-end flow:

```bash
# 1. Submit an application — a borderline profile pauses the workflow in REVIEW
curl -s -X POST http://localhost:8080/predict/graph \
  -H 'Content-Type: application/json' \
  -d '{"customer_data":{"income":8000000,"age":35,"employment_years":0.5,
       "loan_amount":120000000,"loan_term":36,"existing_debt":2000000,
       "credit_history":9,"previous_defaults":2}}'
# → {"thread_id": "run-…", "decision": "REVIEW", "approval_required": true,
#    "ledger_application_id": 1, "ledger_status": "PENDING_REVIEW"}

# 2. Analyst approves — the workflow resumes and the loan is disbursed to the ledger
curl -s -X POST http://localhost:8080/predict/graph/run-…/approve \
  -H 'Content-Type: application/json' -d '{"action":"approve","note":"ok"}'
# → {"decision": "APPROVE", "workflow_complete": true, "ledger_status": "APPROVED",
#    "disbursement": {"contract_code": "HDTD-20260912-0003", "loan_amount": 120000000.0,
#                     "status": "COMPLETED", "ledger_hash": "2f356aaf…"}}

# 3. Inspect the durable ledgers
curl -s http://localhost:8080/api/applications
curl -s http://localhost:8080/api/disbursements   # + total_disbursed
```

**Durable by design — two independent persistence layers:**

| Layer | Storage | Survives restart? | What it holds |
| ----- | ------- | ----------------- | ------------- |
| Loan ledger | SQLite `data/creditflow_ledger.db` (`pipeline/storage/`) | ✅ | Application lifecycle (`PENDING_REVIEW → APPROVED/REJECTED`) + disbursements with per-day contract codes (`HDTD-YYYYMMDD-XXXX`) and a SHA-256 `ledger_hash` over immutable fields — any manual row edit breaks the hash (tamper-evident) |
| Workflow state | `data/creditflow_checkpoints.pkl` (`FileCheckpointSaver`, stdlib-only subclass of LangGraph's `InMemorySaver`) | ✅ | The full paused graph state keyed by `thread_id` — after a server restart, `POST /predict/graph/{thread_id}/approve` resumes the exact paused workflow (audit trail preserved across processes) |

Rejecting (`"action":"reject"`) resumes the workflow to the audit terminal and
writes **no** disbursement — money movement only ever follows an approval.

---

## Model benchmark (real, reproducible)

| Model               | Threshold | Business cost | Val F1 | Val Recall | Test F1 | Test Accuracy |
| ------------------- | --------: | ------------: | -----: | ---------: | ------: | ------------: |
| Logistic Regression | 0.20      | **201.0**     | 0.5694 | 0.80       | 0.5344  | 0.8373        |
| Random Forest       | 0.25      | 233.0         | 0.5483 | 0.71       | 0.5210  | 0.8480        |
| XGBoost             | 0.05      | 273.0         | 0.4721 | 0.72       | 0.4488  | 0.7773        |
| Decision Tree       | 0.05      | 364.0         | 0.3860 | 0.44       | 0.4059  | 0.8400        |

Fresh copy: `models/production/benchmark_results.csv`. Regenerate with
`python scripts/train_models.py`.

**Why this model?** Logistic Regression minimises the cost-aware objective (FN cost 5 >
FP cost 1) on validation and has the highest validation F1 among the field; on the
synthetic proxy its separability is strongest at lower thresholds. Selection is
**evidence-based on business cost + recall**, not "XGBoost is usually better".

---

## Docker

> GenAI explain layer: set `CREDITFLOW_LLM_PROVIDER=cloudflare` + `CLOUDFLARE_*` in `.env`
> to enable real LLM explanations. Without them, the explain falls back to a
> deterministic template (fully offline). Secrets are NOT baked into the image.

```bash
docker build -t creditflow-api .
docker run -p 8080:8080 creditflow-api
curl http://localhost:8080/health
```

Compose (API only by default; optional MLflow UI via profile):

```bash
docker compose up --build            # api on :8080
docker compose --profile mlflow up   # + MLflow UI on :5000
```

> ⚠️ Docker runtime verification requires a running Docker daemon. On this machine the
> daemon was not running during the audit; the image is built from `Dockerfile` and the
> identical service is verified native on :8080. Re-run the three commands above with
> Docker Desktop started to close the containerized demo gap (report §Docker shows the
> expected transcript).

---

## Cloud (Render free tier — config committed, not yet deployed)

`render.yaml` is committed: Render dashboard → New → Web Service → connect repo,
Build `pip install -r requirements.txt`, Start
`uvicorn backend.app:app --host 0.0.0.0 --port $PORT`, health check `/health`.
Acceptance after deploy: `GET <url>/health`, `POST <url>/predict`,
`GET <url>/model/info`. Needs your Render account — no credentials in repo.

> GenAI explain layer on Render: set `CREDITFLOW_LLM_PROVIDER=cloudflare`,
> `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_MODEL` in the
> Render dashboard env vars (`CLOUDFLARE_*` secrets marked `sync: false` in
> `render.yaml` — values live only in your dashboard, never in the repo).
> Default model `@cf/meta/llama-3.2-1b-instruct` is the smallest Meta chat
> model: cheapest per free-tier Neuron budget (10,000 free Neurons/day,
> shared across Workers AI; $0.011/1k Neurons above that on Workers Paid —
> per-model token rates at
> `developers.cloudflare.com/workers-ai/platform/pricing`).
> Verified working against the Workers AI REST API (live `source:
> "langchain_llm"`, ~1s); without credentials the explain falls back to the
> deterministic template (offline-safe).

---

## MLOps status

| Capability              | Status                                                       |
| ----------------------- | ------------------------------------------------------------ |
| MLflow tracking         | Real — experiment `creditflow-risk` (parent + 4 nested runs/model); backend `sqlite:///mlflow.db`, view with `MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui` |
| MLflow registry/version | Real — registered model `creditflow-risk` (latest READY); version in `models/production/meta.json` answers "which model is serving?" via `GET /model/info` |
| Docker                  | Dockerfile + compose; native runtime verified, Docker runtime pending (daemon off) |
| Cloud deployment        | Deployed on Render free tier + Cloudflare Pages; `render.yaml` committed |
| System monitoring       | `GET /metrics`: request/latency/error counters + uptime      |
| ML drift monitoring     | Implemented — `GET /drift`: PSI-based feature + prediction drift vs training reference (NO_DRIFT / DRIFT_DETECTED / INSUFFICIENT_DATA) |
| Approval workflow       | LangGraph state machine with human-approval interrupt; paused state persisted to disk and resumable after a server restart |
| Core-banking ledger     | SQLite `data/creditflow_ledger.db` — loan application lifecycle + disbursements (contract codes, SHA-256 tamper-evident ledger hash); evidence APIs `GET /api/applications`, `GET /api/disbursements` |

---

## Current status & limitations

* The system is **demoable locally end-to-end** (UI → API → real model).
* Data is a synthetic proxy; class-label signal is injected to make benchmarking meaningful.
* Monitoring is in-process demo-level (no Prometheus persistence yet).
* MLflow logging is real: `pip install mlflow==3.16.0`, set
  `MLFLOW_TRACKING_URI=sqlite:///mlflow.db`, then run `python scripts/train_models.py`
  — writes to `mlflow.db`; inspect with `mlflow ui` (experiment `creditflow-risk`,
  registered model `creditflow-risk`). Without mlflow installed, tracking is skipped
  gracefully and the serving path is unaffected.
* ML drift monitoring (`GET /drift`) is implemented: PSI-based detection of data /
  prediction distribution shift vs the training reference. It reports distribution
  drift only — **not** model performance degradation (that requires ground-truth
  labels, unavailable at inference time).
* The approval workflow and ledgers are demo scope: disbursement is recorded as an
  auditable ledger entry (contract + hash) — no external money-transfer or core-banking
  integration. LLM explanations never decide; policy + cost-tuned threshold decide.

See `docs/spec.md` and `docs/qa/manual-acceptance.md` for details and
the manual acceptance test evidence.