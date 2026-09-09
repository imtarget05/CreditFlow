# CreditFlow — Interview Demo Package

> 5-minute live demo script + technical talking points + recovery plan.
> Everything below is backed by **actual evidence** in the repo — no claims
> without a file/test/endpoint to point at.

---

## 1. Five-minute live demo script

### Minute 0–1 — Business problem

> "CreditFlow evaluates loan-application risk. The business cost of a missed
> default (FN) is 5× the cost of wrongly rejecting a good customer (FP), so the
> model and threshold are chosen **recall-centrically**, not by raw accuracy."

Show: `docs/spec.md` §3 (business objective), `pipeline/modeling/threshold.py`
(`DEFAULT_FN_COST = 5.0`, `DEFAULT_FP_COST = 1.0`).

### Minute 1–2 — Live prediction (the "wow" moment)

Open `http://localhost:5173` → preset "Khách hàng lý tưởng" → submit.

> "The UI calls FastAPI → preprocessing → production model → business decision.
> This profile returns probability 0.0668 → **APPROVE**."

Then preset "Khách hàng rủi ro cao" → submit.

> "This one returns probability 0.9793 → **REJECT**."

**Key semantic point** (interview gold):

> "Notice a mid-risk profile (probability 0.4264) returns **REVIEW** even though
> `risk_level` is **LOW**. I deliberately separated risk-band presentation from
> business decisioning — the decision uses a cost-tuned threshold (0.20), while
> the UI risk level keeps fixed LOW/MEDIUM/HIGH buckets. Business logic ≠ model
> probability ≠ UI label."

### Minute 2–3 — Model benchmark + selection

Open the "Model" tab (or `GET /model/info`).

> "We benchmark 4 models — Logistic Regression, Decision Tree, Random Forest,
> XGBoost — on accuracy, precision, recall, F1, and **business cost**."

Show `models/production/benchmark_results.csv`:

| Model | Threshold | Business Cost | Val F1 | Val Recall |
|---|---|---|---|---|
| Logistic Regression | 0.20 | **201.0** | 0.5694 | 0.80 |
| Random Forest | 0.25 | 233.0 | 0.5483 | 0.71 |
| XGBoost | 0.05 | 273.0 | 0.4721 | 0.72 |
| Decision Tree | 0.05 | 364.0 | 0.3860 | 0.44 |

> "Logistic Regression wins on the cost-aware objective. Selection is
> evidence-based on business cost + recall, not 'XGBoost is usually better'."

### Minute 3–4 — MLflow lifecycle

```bash
MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui --port 5000
```

Open `http://localhost:5000` → experiment `creditflow-risk`.

> "Training logs a parent benchmark run + 4 nested model runs (params + metrics),
> artifacts (pipeline, benchmark CSV, meta), and registers the production model.
> The registry answers 'which model is currently serving?' — and `GET /model/info`
> maps the API → registered version → training run."

Point at: registered model `creditflow-risk` (latest version READY).

### Minute 4–5 — Monitoring + drift

```bash
curl http://localhost:8080/drift
```

> "`GET /metrics` covers system observability (requests, latency, errors, uptime).
> `GET /drift` adds ML drift monitoring — PSI-based feature + prediction
> distribution shift vs the training reference. It reports **data drift only**,
> not performance degradation (that needs ground-truth labels we don't have at
> inference time). Statuses: NO_DRIFT / DRIFT_DETECTED / INSUFFICIENT_DATA."

Close with the architecture slide (section 2 below).

---

## 2. Architecture (the one-slide story)

```
BUSINESS
Credit risk decision support (FN cost 5 > FP cost 1)
        ↓
DATA
Pandas / NumPy / EDA / Feature Engineering (5 derived features)
        ↓
ML
Logistic Regression / Decision Tree / Random Forest / XGBoost
        ↓
EVALUATION
Precision / Recall / F1 / Business Cost → cost-aware selection
        ↓
MLOPS
MLflow tracking + registry + SHA-256 provenance
        ↓
APPLICATION
React/Vite → FastAPI → preprocessing → production model → decision
        ↓
DEPLOYMENT
Docker (local) / Render (cloud config committed)
        ↓
OBSERVABILITY
System metrics (/metrics) + ML drift (/drift)
        ↓
ACCEPTANCE
Manual E2E (docs/qa/final-manual-acceptance.md) + 57 automated tests
```

---

## 3. Technical talking points (with evidence)

| Question | Answer | Evidence |
|---|---|---|
| Why Logistic Regression? | Lowest business cost on validation (FN>FP); highest val F1 in the field | `benchmark_results.csv`, `meta.json` `selection_reason` |
| Why F1 / Recall? | Recall-centric: missing a default (FN) costs 5× a false alarm (FP) | `threshold.py` `DEFAULT_FN_COST=5.0` |
| Why threshold 0.20? | Found by cost-minimisation on validation, not a magic number | `find_optimal_threshold()` in `threshold.py` |
| Why separate risk_level and decision? | Presentation bands (fixed) vs business decision (cost-tuned) are different concerns | `predict_service.py` lines 57–59 |
| How is the model versioned? | `meta.json` version + MLflow registry + SHA-256 dataset provenance | `models/production/`, MLflow |
| How is the dataset tracked? | SHA-256 of the CSV logged to MLflow params + `meta.json` | `train_models.py` `_sha256()` |
| How do you prevent leakage? | Train/val/test split before any scaling; scaler fit on train only | `train.py` `stratified_train_val_test_split` |
| What happens when data drifts? | `GET /drift` flags distribution shift (PSI) vs training reference | `pipeline/monitoring/drift.py` |
| How would you retrain? | `python scripts/train_models.py` → new benchmark → new registry version | `scripts/train_models.py` |
| How would you rollback? | MLflow registry has prior versions; redeploy the previous `pipeline.joblib` | MLflow registry |
| Why synthetic data? | Deterministic proxy (seed 42) makes the pipeline reproducible and demoable anywhere | `generate_dataset.csv`, README §proxy |
| What are the limitations? | Synthetic proxy; no real bank data; drift ≠ performance degradation; cloud not deployed | README §limitations |

---

## 4. Demo recovery plan (if something breaks live)

| Failure | Recovery | Prevention |
|---|---|---|
| Backend down (`/health` fails) | `cd /Users/mainguyenbinhtan/Downloads/CreditFlow && python3 -m uvicorn backend.app:app --host 0.0.0.0 --port 8080` | Verify `/health` before demo |
| Frontend down | `cd frontend && npm run dev` | Verify `:5173` before demo |
| Model artifact missing | `python scripts/train_models.py` regenerates `models/production/` | Artifacts are committed; only missing if deleted |
| MLflow UI won't start | `MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui --port 5000` | Set env var (SQLite backend, not file store) |
| Drift endpoint 404 | `reference_stats.json` missing → retrain | Artifact committed; retrain if deleted |
| Docker daemon off | Start Docker Desktop → `docker build -t creditflow-api .` | Pre-demo checklist |
| Cloud endpoint down | `render.yaml` committed; redeploy from dashboard | Health check `/health` |

**Pre-demo checklist** (run 5 minutes before):

```bash
# 1. Backend up
curl http://localhost:8080/health
# 2. Frontend up
curl http://localhost:5173/
# 3. Model loaded (check version in /health response)
# 4. One test prediction
curl -s -X POST http://127.0.0.1:8080/predict \
  -H 'Content-Type: application/json' \
  -d '{"income":2500,"age":32,"employment_years":4,"loan_amount":12000,"loan_term":36,"existing_debt":3500,"credit_history":5,"previous_defaults":0}'
```

---

## 5. Honest positioning for the interviewer

**What to say:**

> "CreditFlow is fully validated locally end-to-end: UI → API → real model →
> decision, with MLflow-tracked training, a registered model, system metrics,
> and ML drift monitoring. Cloud deployment and the browser visual walkthrough
> are the remaining owner-verification steps."

**What NOT to claim:**

- ❌ "It's deployed to production" → ✅ "Cloud config is committed; not yet deployed"
- ❌ "Drift detection means we catch model degradation" → ✅ "It catches data distribution shift; performance degradation needs labels"
- ❌ "It uses real bank data" → ✅ "Deterministic synthetic proxy (seed 42); real-data ingest architecture exists but is isolated"
- ❌ "57 tests = user acceptance" → ✅ "Tests cover internal correctness; user acceptance is manual (see `docs/qa/final-manual-acceptance.md`)"