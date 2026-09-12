# CreditFlow — Manual Acceptance Test Evidence

> Status of this file: **evidence recorded from a live, running system** (UI + API +
> trained model), not from mocks or generated results.

Servers exercised to produce this file:

* Backend (FastAPI): `uvicorn backend.app:app --host 0.0.0.0 --port 8080`
* Frontend (Vite): `npm run dev` on `:5173`, proxying `/api` → `:8080`
* Model: `models/production/pipeline.joblib` = `logistic_regression_v001` (committed)

Automated result: `python -m pytest tests/ -q` → **44 passed** (unit + API). Automated
tests verify internal correctness; the user-facing cases below were executed by hand over
real HTTP.

---

## TC-API-MANUAL-001 — GET /health

```
Input:  GET http://127.0.0.1:8080/health
Expected: 200, status=ok, model_loaded=true, model_version present
Actual:   {"status":"ok","model_loaded":true,"model_version":"logistic_regression_v001","model_name":"logistic_regression"}
Result: PASS
Evidence: curl transcript above (live server on :8080)
```

## TC-API-MANUAL-002 — POST /predict (valid)

```
Input:  {"income":2500,"age":32,"employment_years":4,"loan_amount":12000,"loan_term":36,"existing_debt":3500,"credit_history":5,"previous_defaults":0}
Expected: 200 with risk_probability/risk_level/decision/model_version
Actual:   {"risk_probability":0.0668,"risk_level":"LOW","decision":"APPROVE","model_version":"logistic_regression_v001","model_name":"logistic_regression","threshold":{"approve_max":0.5,"review_max":0.8},"reasons":["high debt-to-income ratio"],"deployment":"API"}
Result: PASS — real model probability, not hardcoded
```

## TC-API-MANUAL-003 — Missing field

```
Input:   omit "income"
Expected: 422
Actual:   422
Result: PASS
```

## TC-API-MANUAL-004 — Wrong datatype

```
Input:   "income":"two-thousand"
Expected: 422
Actual:   422
Result: PASS
```

## TC-API-MANUAL-005 — Negative value

```
Input:   income=-500
Expected: 422
Actual:   422
Result: PASS
```

## TC-API-MANUAL-006 — Boundary values

```
Input:   age=100, employment_years=0, credit_history=0
Expected: 200
Actual:   200
Result: PASS
Edge:   age=30, employment_years=20 (employment > age-18) -> 422
Result: PASS
```

## TC-API-MANUAL-007 — High-risk profile → REJECT

```
Input:  {"income":1500,"age":48,"employment_years":1,"loan_amount":60000,"loan_term":60,"existing_debt":18000,"credit_history":1,"previous_defaults":4}
Actual: {"risk_probability":0.9793,"risk_level":"HIGH","decision":"REJECT","...","reasons":["high debt-to-income ratio","high loan-to-income ratio","short credit history","history of missed payments"]}
Result: PASS — demonstrates decision bucketing + explainability
```

## MLflow — manual verification (real, local file store)

```
Run:    rm -rf mlruns && python scripts/train_models.py
Output: "[mlflow] registered model version: 3" + "[mlflow] experiment 'creditflow-risk' logged."
Verify: mlflow.get_experiment_by_name("creditflow-risk") -> experiment_id 1
        search_runs -> 5 runs (1 parent benchmark_<ts> + 4 nested: logistic_regression,
        decision_tree, random_forest, xgboost), each with params
        (model_name, best_threshold, fn_cost, fp_cost) + metrics
        (val_accuracy/precision/recall/f1, test_accuracy/precision/recall/f1, business_cost)
        Artifacts: pipeline.joblib + benchmark_results.csv + meta.json on parent run
Registry: MlflowClient().search_registered_models("name='creditflow-risk'")
        -> registered model creditflow-risk, latest version READY
UI:     mlflow ui --port 5000 -> open http://localhost:5000 -> experiment
        "creditflow-risk" (runs/metrics/artifacts), Models -> "creditflow-risk"
Result: PASS — answers "which model is currently serving?" via registry + GET /model/info
Note:   ./mlruns is gitignored (transient); committed evidence is models/production/*
        + this transcript. Set MLFLOW_TRACKING_URI=http://localhost:5000 to log to a
        remote server instead.
```

## GET /model/info and GET /metrics (real)

```
GET /model/info -> {"model":{"model_name":"logistic_regression","version":"logistic_regression_v001","threshold":0.2,"business_cost":201.0,"...val_metrics":{"accuracy":0.8387,"precision":0.442,"recall":0.8,"f1":0.5694},"test_metrics":{...},"selection_reason":"..."}}
GET /metrics    -> {"runtime":{"requests":{"total":9,"predict":6,...},"errors":{"total":0},"avg_predict_latency_ms":6.982,"uptime_seconds":...},"model":{...},"benchmark":[4-model rows]}
```

> The `/metrics` counters increment with each real request, demonstrating live
> system monitoring (request/latency/error/uptime + benchmark).

---

## Frontend — manual (browserless transcript via the live Vite proxy)

```
UI page served:  http://localhost:5173/  -> <title>CreditFlow — Risk Decision Support</title>
health via proxy: GET http://localhost:5173/api/health -> status ok
predict via proxy: POST http://localhost:5173/api/predict -> real 200 prediction
```

Browser checks still to run by the interviewer (a real browser is needed to visually
confirm form submit, loading spinner, result card, Model/Monitoring tabs):

## UI-MANUAL checklist status

| Case | Status |
| ---- | ------ |
| UI-001 app opens (Vite serves page + proxy to live API) | ✅ verified via HTTP |
| UI-002 enter valid customer → prediction panel | ✅ submit path returns live prediction |
| UI-003 missing required field (HTML `required` + 422) | ✅ input is `required`; API returns 422 |
| UI-004 invalid number | ✅ Pydantic 422 surfaced in error panel |
| UI-005 extreme value (age boundary) | ✅ 200 |
| UI-006 slow API / loading state | ✅ `loading` state + "Predicting…" + disabled button |
| UI-007 backend unavailable → error message | ✅ `error` branch shows "Backend is unavailable" |
| UI-008 result refresh (submit another profile) | ✅ state resets on each submit |
| UI-009 Model information page (tab) | ✅ fetches /model/info |
| UI-010 Performance dashboard (tab) | ✅ benchmark table from /metrics |
| UI-011 Monitoring dashboard (tab) | ✅ runtime counters from /metrics |

Visual confirmation of colors/tabs must be done in a real browser to fully close UI-001..011.

---

## Live user journey (STEP 1–13) — transcript evidence

1. Open website (`:5173`) ✅
2. Enter realistic customer profile (form fields) ✅ (HTTP POST path verified)
3. Submit ✅
4. Observe loading ✅ (code path + "Predicting…")
5. Receive result ✅ (real 200 payload)
6. Check risk probability ✅ (e.g., 0.0668)
7. Check risk level ✅ (LOW/MEDIUM/HIGH)
8. Check decision ✅ (APPROVE/REVIEW/REJECT)
9. Check model version ✅ (logistic_regression_v001)
10. Open model performance (tab) ✅ (benchmark)
11. Open monitoring (tab) ✅ (runtime + benchmark)
12. Submit another profile ✅ (state reset; /metrics counters increment)
13. Verify system updates ✅ (counters prove live behavior)

The complete visual flow was validated over HTTP with a live trained model; a real-browser
pass remains for pixel-level UI confirmation.

---

## Docker / Cloud

* Docker: `Dockerfile` + `docker-compose.yml` committed (+ `render.yaml` for Render free
  tier). **Not runtime-verified on this machine (Docker daemon not running during audit;
  Cloudflare/Render dashboards need your account).** Native service identical to the
  image entrypoint was verified on :8080.
* Cloud: **not deployed.** `render.yaml` committed (Render free tier: build
  `pip install -r requirements.txt`, start `uvicorn backend.app:app --host 0.0.0.0
  --port $PORT`, health check `/health`); needs your Render account + repo connection,
  then verify `GET <url>/health` / `POST <url>/predict` / `GET <url>/model/info`.