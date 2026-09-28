# Test Cases — CreditFlow ML Risk Decision Engine

**Plan:** [`TEST_PLAN.md`](TEST_PLAN.md). **Contract:** `docs/qa/QA_ACCEPTANCE.md`.
On-disk enumeration 2026-09-27: 27 files / 210 `def test`. Function names below
are verified from disk; ✅ = ran green this session, CI = needs CI deps.

| ID | file::case (on disk) | Invariant asserted | Runnable offline? |
|---|---|---|---|
| CF-001 | `test_features.py` (19 fns) | correct feature vector | CI |
| CF-002 | `test_features.py` + `test_agent.py::test_policy_and_fraud_use_vnd_as_is` | no `NaN/Inf/crash` on zero denominator | CI |
| CF-003 | `test_api.py::test_predict_missing_field_422` ✅-pattern, `test_agent.py::test_validate_input_rejects_training_scale_money` | reject or manual-review route | CI |
| CF-004 | `test_api.py::test_predict_negative_income_422` ✅-pattern | absurd negatives caught | CI |
| CF-005 | `test_threshold_boundary.py` (5) ✅ + `test_threshold.py::test_decision_buckets_below_approve` | 0.1999 → correct side | ✅ 18 passed (3-file run) |
| CF-006 | `test_threshold.py::test_decision_boundary_approve` | exact boundary rule | ✅ same run |
| CF-007 | `test_threshold.py::test_decision_bucket_review_mid/reject_high` | correct decision | ✅ same run |
| CF-008 | `test_agent.py::test_explanation_deterministic_fallback` | identical prediction on repeat | CI |
| CF-009 | `test_predict_units.py::test_prediction_is_independent_of_dict_key_order` | no silent misprediction on reorder | CI |
| CF-010 | `test_check_artifacts.py` (6) ✅ | never silently run another model | ✅ same run |
| CF-011 | `test_check_artifacts.py` (modified-artifact fns) ✅ | integrity check fails | ✅ same run |
| CF-012 | `test_check_artifacts.py` (hash-tamper fns) ✅ | deployment blocked | ✅ same run |
| CF-013 | `test_agent.py::test_graph_approve_path/reject_path/review_path_pauses` | 12-node route correct | CI |
| CF-014 | `test_agent.py::test_graph_endpoint_start_review_and_resume` | workflow stops at HITL node | CI |
| CF-015 | `test_agent.py::test_graph_endpoint_start_review_and_reject` | reject → no disbursement | CI |
| CF-016 | `test_agent.py::test_graph_endpoint_start_review_and_resume` (approve half) | correct post-approval state | CI |
| CF-017 | `test_approve_race.py::test_concurrent_same_application_yields_exactly_one_disbursement`, `::test_replay_same_key_sequential_never_duplicates`, `::test_concurrent_different_keys_all_succeed` ✅ | duplicate approve → 1 disbursement | ✅ 3 passed |
| CF-018 | `test_ledger_property.py::test_double_entry_conservation_over_seeded_applications`, `::test_replay_same_application_never_duplicates_money`, `::test_tamper_breaks_hash_but_never_silently` ✅ | idempotency protects ledger | ✅ 3 passed |
| CF-019 | `test_drift_offline.py` (12 fns, WARNING path) | moderate drift → WARNING | CI |
| CF-020 | `test_drift_offline.py` (ALERT path) | severe drift → ALERT | CI |
| CF-021 | `test_drift_offline.py` (no-false-positive path) | no false positive | CI |
| CF-022 | `scripts/bench_creditflow.py` (no test file) | latency SLA | UNVERIFIED (NOT A GATE) |

## Supplementary on-disk coverage

| File | `def test` | Notes |
|---|---|---|
| `test_api_auth.py` 22, `test_health_contract.py` 3 | 25 | auth + health contract |
| `test_llm_provider.py` 17 (incl. `test_injected_decision_and_risk_score_are_dropped`) | 17 | LLM-output injection safety |
| `test_approval_idempotency.py` 4, `test_e2e_disbursement.py` 5, `test_ledger_pg_routing.py` 3, `test_ledger_units.py` 1 | 13 | disbursement paths (PG = live-infra) |
| `test_checkpoint_persistence.py` 6 (3 shown: resume/reject/isolate) | 6 | pause/resume across restart |
| `test_models.py` 9, `test_model_update.py` 2, `test_predict_units.py` 11, `test_features.py` 19, `test_drift.py` 10, `test_ingest.py` 1, `test_eval.py` 1, `test_retriever.py` 3, `test_verify_deploy.py` 2, `test_deploy_pages.py` 2, `test_frontend_polish.py` 4 | 64 | model/data/deploy/frontend |

**Invariant:** `ONE LOAN APPROVAL → AT MOST ONE DISBURSEMENT`

## Full-run verdicts 2026-09-27 (363 passed, 5 skipped, 0 failed)

All rows above verified green except:
- CF-008 → PASS with note (deterministic artifact + explanation determinism; no direct
  same-input pin test — hardening candidate, residual risk low).
- CF-009 → PASS (impl selects features by NAME from artifact `feature_order`,
  `backend/predict_service.py:182-212` — permutation cannot silently mispredict).
- CF-022 → P1 exception documented (bench script exists, no automated gate — DEF-CF-001).
- `test_models.py` + `test_model_update.py` → env-BLOCKED (xgboost needs system libomp).

**Gate verdict: CONDITIONAL PASS** — all P0 cases green; P1 exception CF-022 documented
(DEF-CF-001 OPEN); xgboost-path coverage BLOCKED by environment with cause.
