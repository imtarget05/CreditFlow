# REPAIR PLAN — CreditFlow

Audit: 2026-09-25. This plan reflects an audit in which the code was **cloned, installed, built and executed** on Windows. Every "VERIFIED" below means it was run, not read.

---

## Current State

FastAPI + scikit-learn/XGBoost credit-risk platform. Real ML pipeline (4 candidate models, cost-sensitive threshold selection), a real 12-node LangGraph workflow with a human-in-the-loop interrupt, and a real tamper-evident SQLite ledger. React/Vite frontend. Render + GitHub Pages + a broken Azure workflow.

**The repository did not run when cloned.** The application refused to start and 3 of 4 test modules errored during collection. Root cause, proved by measurement:

`backend/predict_service.py:82` `validate_model_bundle()` recomputes SHA-256 for every artifact listed in `models/production/manifest.json` and raises `ArtifactContractError` on mismatch. The manifest values are correct for the committed blobs. But the repository has **no `.gitattributes`**, so on any Windows clone `core.autocrlf=true` rewrites the text artifacts from LF to CRLF and changes their hash. `app.state.pipeline` then never loads, the FastAPI lifespan raises, and every test module that imports the app errors at collection.

| Artifact | manifest / blob SHA-256 | working-tree SHA-256 | match |
|---|---|---|---|
| `benchmark_results.csv` | `38c9e098…f23` | `b0c48101…7c9f1` | NO |
| `benchmark_results.json` | `5ea5b078…c90` | `02cf5dab…24c78` | NO |
| `meta.json` | `c056b3c2…7fa77aa` | `f9d2d7df…edebbd` | NO |
| `reference_stats.json` | `757c86a0…d268c2e` | `4a448ec5…c95449` | NO |
| `pipeline.joblib` | `119f3b3c…48774` | `119f3b3c…48774` | YES (binary, unaffected) |

`pipeline.joblib` matching while every text artifact fails is the signature of line-ending conversion, not corruption. The same defect also broke `tests/test_models.py:167` (the dataset SHA pinned in `meta.json`).

---

## Verified Working Features

All of the following were read in source and are exercised by the suite that now passes.

- **Cost-sensitive model selection** over Logistic Regression, Decision Tree, Random Forest and XGBoost — `scripts/train_models.py:143-182`; `pipeline/modeling/threshold.py:91-128`; `DEFAULT_FN_COST=5.0` / `DEFAULT_FP_COST=1.0` at `threshold.py:22-23`; selected threshold `0.2` and `business_cost 201.0` recorded in `models/production/meta.json:5-8`.
- **12-node LangGraph workflow with a real human interrupt** — `pipeline/agent/graph.py:104-145` (exactly 12 `add_node` calls), `interrupt()` at `pipeline/agent/nodes.py:498`, resume via `Command(resume="approve")` at `backend/app.py:622`, file-backed checkpointer wired at `backend/app.py:386-394`.
- **Tamper-evident ledger** — contract code `HDTD-%Y%m%d-%04d` at `pipeline/storage/ledger.py:504-513`; SHA-256 at `:516-521`; verification with `hmac.compare_digest` at `:524-543`; schema with `UNIQUE(application_id)`, `UNIQUE(contract_code)`, `CHECK` constraints, 4 indexes, FK `REFERENCES loan_applications(id)` at `:112`, and `PRAGMA foreign_keys=ON` at `:85`.
- **PSI drift detection** with an `INSUFFICIENT_DATA` guard below 50 samples — `pipeline/monitoring/drift.py:92-126,137-211`; endpoint `backend/app.py:279-305`.
- **Deterministic fraud flags upstream of the ML decision** — `pipeline/agent/nodes.py:246-254` calling `pipeline/agent/fraud.py`, ordered before `decision` at `nodes.py:420-434`.
- **Bundle integrity gate** — `validate_model_bundle()` checks manifest version, `meta.json` equality, required fields, `feature_order` against the serving schema, per-artifact SHA-256, and scikit-learn major/minor compatibility (`backend/predict_service.py:82-132`).
- **Production-simulation gate is real and CI-enforced** — the CIC simulator (`pipeline/gateways/cic_gateway.py:133-158`) raises `GATEWAY_UNAVAILABLE` when `CREDITFLOW_ENV=production` (`pipeline/agent/nodes.py:278-279`), and `.github/workflows/ci.yml:46-61` asserts it.
- **161 real test functions** across `tests/`, with `integration` and `slow` markers genuinely used (`pytest.ini:2-4`) and `slow` isolated into its own CI job (`ci.yml:63-81`).

---

## Broken Features

### B1 — Cross-platform failure of the model-bundle integrity gate · **REPAIRED**

**Status: REPAIRED and VERIFIED.** A 9-line `.gitattributes` was added, with the root cause documented in the file itself:

```
models/production/** -text
data/*.csv -text
```

Verified after the change, on a checkout with `core.autocrlf=true`: all five artifacts reproduce their manifest SHA-256 exactly, `data/creditflow_dataset.csv` reproduces the `b65728f8…` value pinned in `meta.json`, and `pytest` reports **164 passed, 1 skipped, 0 failed** (was: 3 collection errors + 1 failure).

No history was rewritten. The blobs were already LF; only the checkout behaviour changed.

### B2 — The Azure deploy workflow is green while deploying nothing

`.github/workflows/deploy-azure.yml:41,48` gate on `if: env.AZURE_CREDENTIALS != ''`, but `AZURE_CREDENTIALS` is only defined as a **step-level** env at `:43,50`. The `if` is evaluated against an empty/absent context, so `azure/login` and the deploy step are always skipped — and the job still reports success. Combined with a 3-test subset at `:37`, this produces a green "Azure deploy" badge that deploys nothing.

`NOT VERIFIED` whether any Azure deploy has ever run.

---

## Half-implemented Features

### H1 — The PostgreSQL persistence path is dead code

`pipeline/storage/ledger.py:245-257` `init_db_pg()` and the DDL at `:184-242` have **no caller anywhere in the repository**. The Postgres branch of `_connect:76-86` is only reached by a monkeypatch test (`tests/test_ledger_pg_routing.py:8`). Any claim that this system persists durably to Postgres is `NOT VERIFIED`.

### H2 — MLflow is wired but silently self-disabling

Logging code is real (`scripts/train_models.py:284-348`, including `mlflow.register_model` at `:341`), but it self-skips when the package is absent (`:296-301`). No `mlruns/`, no run artifact, no UI output — and `.gitignore:17-18` excludes them. The "model registry" claim is `CONFIGURED_ONLY`.

### H3 — The local-vLLM explainer is a self-declared stub

`pipeline/agent/llm_provider.py:259-291` `try_local_vllm_explain` carries the docstring "**example stub** demonstrating Private Enterprise LLM integration". It returns `None`, and the caller falls back to a template. No vLLM is ever contacted. This is honestly labelled, but it is not a feature.

### H4 — The disbursement "node" does not disburse

`pipeline/agent/nodes.py:558` writes an audit string containing the word "stub". The node itself only generates a VietQR payload and agreement text (`nodes.py:540-555`); the actual ledger write happens later at `backend/app.py:651`. Wording only — the flow works — but it will confuse a reader tracing the code.

---

## Documentation Claims Not Verified

| Claim | Location | Status |
|---|---|---|
| "118 tests (103 fast + 12 slow + 3 live-network)" | `README:10` | **FALSE / STALE.** 161 `def test_` functions exist. The marker split is real; the numbers are not. |
| "Deployment … Azure Container Apps" | `README:204` | **NOT VERIFIED / broken gate.** See B2. |
| Postgres durable storage | implied by `_PG_DURABLE_DDL` | **NOT VERIFIED.** See H1. |
| "MLflow model registry" | `README` | **CONFIGURED_ONLY.** See H2. |
| `pipeline/agent/explanations.py:is_public_cloud` | `README` | **Imprecise.** `is_public_cloud` is a *local variable* at `explanations.py:228`, not a module function. The guard itself is real and tested (`tests/test_llm_provider.py:297,310`). |
| Selected-model metrics (LR cost 201, recall 0.80, F1 0.57) | `README` | **PARTIALLY VERIFIED.** Values exist in `meta.json:6,10-14` and `benchmark_results.json:2-15`, and `tests/test_models.py:191` re-derives them. There is no committed run log. |

---

## Security Problems

### S1 · P0 — No authentication or authorization on a money-moving API

`backend/app.py:531-694`: **any anonymous caller** can `POST /predict/graph/{thread_id}/approve` and cause a `disbursements` row to be written (`:651` → `ledger.py:546`). `approver_id` is taken from the request body (`:415`) and defaults to the literal `"supervisor_on_duty"`.

`GET /applications` (`:771`) and `GET /disbursements` (`:782`) return full customer PII and money totals to anyone.

There is no RBAC on the audit trail, and no rate limiting.

This is the single most damaging item in the repository. A money-moving endpoint with a public `approve` is the first thing a technical reviewer will find.

### S2 · P1 — CORS wide open

`allow_origins=["*"]` with all methods and headers at `backend/app.py:180-186`. `allow_credentials=False`, so it is not the credential-reflection variant, but it still means any origin can call a money-moving API.

### S3 · Verifiable positives — keep these

- **No SQL injection.** All ledger SQL is parameterised (`ledger.py:271-276,355-371`).
- **Strong input validation.** Pydantic v2 field constraints plus cross-field validators (`app.py:64-88`) and an explicit money-unit contract (`backend/predict_service.py:159-161`) that raises a clear 422 instead of silently mis-scaling a profile.
- **No committed secrets.** No `.env` is committed; `.gitignore:56-77` blocks `.env*`, `*.pem`, `*secret*`; `render.yaml:28-31` uses `sync: false`. The VCR cassettes (`tests/cassettes/*.yaml:3`) document that `Authorization` is filtered — no token material in the repo.
- **No sensitive logging.** `/llm/info` reports provider and model only, never keys (`app.py:315-358`).
- **CIC simulator is properly gated** in production and CI-enforced. `DEV_ONLY (well-guarded)` — do not "fix" this.

---

## Testing Gaps

- **No authentication tests**, because there is no authentication. Once S1 is fixed, the RBAC matrix needs its own tests before it can be claimed.
- **`init_db_pg` is untested and uncalled** (H1). Either wire it and test it, or delete it. Dead code that looks like a feature is worse than no code.
- `tests/test_deploy_pages.py:84-113` performs **real HTTP calls to `https://creditflow-api-9z1v.onrender.com`** (`:25`). It is correctly marked `@pytest.mark.integration` and excluded by CI (`ci.yml:39`), but it is a live-network test against a URL that a future reader will assume still works.
- `<10` tests are file/CLI-shape checks rather than behaviour (`test_deploy_pages.py:59-83`, `test_verify_deploy.py:16,76`). Acceptable, but do not quote them as coverage.
- No load/concurrency test on the `approve` path, which is the one that matters.

---

## Deployment Gaps

| Target | Classification | Evidence |
|---|---|---|
| GitHub Pages (web) | `CD_IMPLEMENTED` + build asserted in CI (`ci.yml:105-109`) | Real |
| GHCR image push | `CD_IMPLEMENTED` | Real |
| Render API | `CONFIGURED` | `render.yaml`; `notify-render` in `cd.yml:136-151` is **echo-only with the curl commented out** |
| Azure Container Apps | **BROKEN GATE** | See B2 |
| **CD_VERIFIED** | **NOT VERIFIED** | No post-deploy health assertion that actually runs |

`docker-compose.yml:19-20` uses `env_file: .env`, so `docker compose up` fails on a fresh clone with no committed `.env`. Provide a `.env.example`.

---

## Recruiter-facing Problems

1. **A money-moving API with no authentication.** Opens the interview with the worst possible finding.
2. **A green "Azure deploy" badge that deploys nothing.** If anyone checks the Actions tab, that is a credibility loss out of proportion to its size.
3. **"Tests-118" on a repo with 161 tests.** In a project whose entire pitch is rigour, a wrong number on the front page is the thing people remember.
4. **Dead Postgres code plus a `mlruns/` gitignore** reads as "I set this up and moved on" rather than "I finished this".
5. **The winning claim is the one with no artefact.** "Logistic Regression beat XGBoost on a custom business cost metric" is a genuinely interesting, differentiating claim for a junior portfolio — and it is currently backed only by a committed JSON file. Committing the training run output would make it a story instead of an assertion.

---

## Repair Tasks

### P0 — blocking

- [x] **P0-1 Add `.gitattributes` pinning content-addressed artifacts as `-text`.** *(DONE — verified 164 passed, 1 skipped, 0 failed.)*
- [ ] **P0-2 Add authentication to the API.** Minimum viable: a shared-secret dependency on `backend/app.py` reading `CREDITFLOW_API_KEY`, returning 401 when unset-in-production and using `hmac.compare_digest` for comparison. Test: no key → 401; wrong key → 401; correct key → 200.
- [ ] **P0-3 Add authorisation to `POST /predict/graph/{thread_id}/approve`.** `approver_id` must come from the authenticated identity, never from the request body. Test: a non-approver role gets 403 and **no `disbursements` row is written** (assert on the row count, not just the status code).
- [ ] **P0-4 Protect `GET /applications` and `GET /disbursements`.** These return customer PII. Either authenticate them or redact the fields.

### P1 — important

- [ ] **P1-1 Fix `deploy-azure.yml`.** Move `AZURE_CREDENTIALS` to workflow/job `env:`, or replace the condition with an explicit `if: vars.AZURE_DEPLOY_ENABLED == 'true'`. Verify by reading the rendered workflow and confirming the condition can actually be true.
- [ ] **P1-2 Reconcile the test count.** Replace the `Tests-118` badge with the real number, or delete the badge. A count that is not reproducible is worse than no count.
- [ ] **P1-3 Wire or delete `init_db_pg`.** If wiring: add a migration test that actually creates the schema on a real Postgres service. If deleting: remove `ledger.py:184-257` and the Postgres branch of `_connect`.
- [ ] **P1-4 Make MLflow either real or absent.** Install it as a hard dependency in `requirements.txt` so the logging path cannot silently skip, or remove the registry claim from the README.
- [ ] **P1-5 Commit a reproducible training artefact.** Store `scripts/train_models.py` stdout + the resolved environment in `docs/evidence/`. This turns the strongest claim from an assertion into a demonstration.
- [ ] **P1-6 Add a `.env.example`** so `docker compose up` works on a fresh clone.

### P2 — nice-to-have

- [ ] **P2-1** Fix the "disbursement stub" wording at `pipeline/agent/nodes.py:558` so a reader tracing the flow is not misled.
- [ ] **P2-2** Correct the `is_public_cloud` reference in the README to a line number, not a function name.
- [ ] **P2-3** Mark `tests/test_deploy_pages.py` as requiring network explicitly in its docstring, and consider gating it behind an env var so a future reader does not assume it is hermetic.
- [ ] **P2-4** Add a load test on the approve path.
- [ ] **P2-5** Tighten CORS to `CORS_ORIGINS` in the style of `ai-product-recommender` (`src/api/main.py:86-94`), which already does it correctly.
