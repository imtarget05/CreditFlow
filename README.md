# CreditFlow — ML Risk Decision Support System

**Production-oriented ML application** that evaluates the risk of a credit / loan
application from tabular data, trains and benchmarks 4 classical ML models, versions
the serving model, and exposes a real prediction service through FastAPI + a React UI.

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
                                              ├─ /health     service + model status
                                              ├─ /predict    real ML prediction
                                              ├─ /model/info production model + metrics
                                              ├─ /metrics    runtime + benchmark
                                              └─ /drift      ML drift monitoring (P11)
FastAPI ─► Prediction Service ─► preprocessor + model (models/production/)
Training: scripts/train_models.py → data/creditflow_dataset.csv
          → models/production/{pipeline.joblib, meta.json, benchmark_results.csv, reference_stats.json}
```

## Repository layout

```
backend/            FastAPI app + prediction service
frontend/           React + Vite UI
pipeline/           data gen, validation, feature engineering, modeling, threshold
scripts/            train_models.py (benchmark + artifact + optional MLflow)
models/production/  trained model + meta + benchmark results (committed)
data/               synthetic proxy dataset (committed)
tests/              unit + API tests (pytest)
notebooks/          EDA + model benchmark notebooks
Dockerfile          inference service container
docker-compose.yml  local orchestration (api + optional mlflow)
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

---

## MLOps status

| Capability              | Status                                                       |
| ----------------------- | ------------------------------------------------------------ |
| MLflow tracking         | Real — experiment `creditflow-risk` (parent + 4 nested runs/model); backend `sqlite:///mlflow.db`, view with `MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui` |
| MLflow registry/version | Real — registered model `creditflow-risk` (latest READY); version in `models/production/meta.json` answers "which model is serving?" via `GET /model/info` |
| Docker                  | Dockerfile + compose; native runtime verified, Docker runtime pending (daemon off) |
| Cloud deployment        | Not deployed — `render.yaml` committed (free tier); see roadmap |
| System monitoring       | `GET /metrics`: request/latency/error counters + uptime      |
| ML drift monitoring     | Implemented — `GET /drift`: PSI-based feature + prediction drift vs training reference (NO_DRIFT / DRIFT_DETECTED / INSUFFICIENT_DATA) |

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

See `docs/spec.md`, `plans/prds/`, and `docs/qa/manual-acceptance.md` for details and
the manual acceptance test evidence.
```