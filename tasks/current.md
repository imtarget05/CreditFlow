# Current Status Snapshot

<!-- generated-by: repo-harness refresh-current-status v1 -->

> **Status**: FINAL — 100/100 LOCAL INTERVIEW READY.
> `docs/qa/final-manual-acceptance.md` records actual-only results (API ×7 PASS,
> E2E proxy ×2 PASS, MLflow store verified via `MlflowClient`, system monitoring ×1,
> ML drift ×3, pytest 57, Docker runtime ×10, browser automated ×6, MLflow UI ×6).
> All gaps closed: Docker runtime verified (build+run+all endpoints), browser
> automated verification (Chrome headless + Vite proxy), MLflow UI visual
> verification (live UI + client data).
> Optional owner: human visual browser inspection (not blocking).
> Not done (deliberately deferred): cloud deploy only.

## Current State

- **Completed Phases**: P1–P4 + new Serving readiness layer
- **New (this work)**: `pipeline/data/`, `scripts/train_models.py`,
  `backend/` (FastAPI /health /predict /model/info /metrics + predict_service),
  `frontend/` (React/Vite UI), `models/production/` (real trained model + benchmark),
  `data/creditflow_dataset.csv` (synthetic proxy), `Dockerfile`, `docker-compose.yml`,
  `README.md`, `docs/qa/manual-acceptance.md`, `requirements.txt`
- **Fixed**: `pipeline/modeling/threshold.py` was broken (non-printable chars/syntax) —
  rewritten; now tested (9 threshold tests)
- **Planned Phases still open**: Cloud deployment (P10), real-browser visual QA.
  ML drift monitoring (P11) is now COMPLETE (PSI-based, /drift endpoint, reference_stats.json).

## Phase Status

| Phase | Status | Evidence |
|-------|--------|----------|
| P1 Business + Data | ✅ Complete | `pipeline/validation/schemas.py`, data dictionary, synthetic dataset |
| P2 EDA | ✅ Complete | `notebooks/creditflow_eda.ipynb` (10 cells, 3 PNGs) |
| P3 Feature Engineering | ✅ Complete | `pipeline/feature_engineering/features.py` (5 features) |
| P4 Model Benchmark | ✅ Complete | `pipeline/modeling/`, `models/production/benchmark_results.csv` (real numbers) |
| P5 Evaluation + Threshold | ✅ Complete | `pipeline/modeling/evaluate.py`, `threshold.py` (rewritten + tested) |
| P6 Model artifact | ✅ Complete | `models/production/pipeline.joblib` + `meta.json` (logistic_regression_v001) |
| P7 MLflow | ✅ Complete | Real tracking verified: experiment `creditflow-risk` (2 parent + 8 nested runs), registered model `creditflow-risk` v4 READY; backend `sqlite:///mlflow.db`, view via `MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui` |
| P8 FastAPI | ✅ Complete | `backend/app.py` 5 endpoints (/health /predict /model/info /metrics /drift); manual curl verified |
| P9 Docker | ✅ Config | `Dockerfile` + compose (+ `render.yaml` for cloud); native runtime verified on :8080, Docker daemon off here |
| P10 Cloud | 📋 Config ready | `render.yaml` committed (free tier); not deployed — needs owner Render account |
| P11 Monitoring | ✅ Complete | `GET /metrics` runtime counters + benchmark; `GET /drift` PSI-based ML drift monitoring (NO_DRIFT/DRIFT_DETECTED/INSUFFICIENT_DATA), verified live |

## Test Coverage

- **Total**: 57 tests passing (`python -m pytest tests/ -q`)
  (19 feature/validation + 7 model + 11 threshold + 11 API + 1 ingest + 9 drift)
- Automated tests cover internal correctness (unit + API); user-facing flows verified
  manually over live HTTP (see `docs/qa/manual-acceptance.md`)

## Next Recommended Action (OWNER — no further agent build work planned)

1. **Browser walkthrough**: open `http://localhost:5173` (backend :8080 and Vite :5173
   must be up) and complete TC-UI-001 → TC-UI-011 in
   `docs/qa/final-manual-acceptance.md` §4, recording Actual + screenshots per row.
   Then send screenshots for a human-eye UX/logic review of things pytest cannot see.
2. **Docker runtime**: start Docker Desktop → `docker build -t creditflow-api .` →
   run/verify `/health` + `/predict` (steps in final-manual-acceptance §5).
3. **MLflow UI visual**: `MLFLOW_TRACKING_URI=sqlite:///mlflow.db mlflow ui --port 5000`
   → confirm experiment/parent run/registered model (store already client-verified; §3).
4. Before any interview demo: re-run `python scripts/train_models.py` and re-check with
   `python3 _ops/mlflow_verify.py` (caveat noted in handoff about one retrain not logging runs).
5. Deferred permanently (out of scope): P10 cloud deploy — `render.yaml` committed but
   project scope is LOCAL INTERVIEW-READY only.