# Traceability Matrix — CreditFlow

`Requirement → Business Rule → Test Case → Automated Test → Execution Evidence`

| Business invariant | Test Case | Automated test (exact node, on disk) | Source file | Evidence |
|---|---|---|---|---|
| NONEGATIVE_LEDGER (one approval → ≤1 disbursement; replay never duplicates money) | CF-017, CF-018 | `test_concurrent_same_application_yields_exactly_one_disbursement`, `test_replay_same_key_sequential_never_duplicates`, `test_double_entry_conservation_over_seeded_applications`, `test_replay_same_application_never_duplicates_money`, `test_tamper_breaks_hash_but_never_silently` | `tests/test_approve_race.py`, `tests/test_ledger_property.py` | VERIFIED 2026-09-27 (3 + 18-run) |
| THRESHOLD_020 (0.1999/0.2000/0.2001 exact) | CF-005–007 | `test_threshold_boundary.py` (5 fns) + `test_threshold.py` boundary fns | `tests/test_threshold_boundary.py`, `tests/test_threshold.py` | VERIFIED 2026-09-27 (in 18-run) |
| ARTIFACT_PINNED (missing/tampered → blocked) | CF-010–012 | `test_check_artifacts.py` (6 fns) | `tests/test_check_artifacts.py` | VERIFIED 2026-09-27 (in 18-run) |
| NO_SILENT_MISCALE (VND↔training scale) | CF-001, CF-003, CF-009 | `test_prediction_is_independent_of_dict_key_order`, `test_training_scale_payload_is_rejected_not_rescaled`, `test_predict_negative_income_422`; impl selects by NAME (`backend/predict_service.py`: `fe[feature_order]`, missing → hard error), so column permutation cannot silently mispredict | `tests/test_predict_units.py`, `tests/test_api.py` | VERIFIED 2026-09-27 (363-run; code ref `predict_service.py:182-212`) |
| DRIFT_GATED (WARNING vs ALERT, no false +) | CF-019–021 | `test_drift_offline.py` (12 fns) + `test_drift.py` (10 fns) | `tests/test_drift_offline.py`, `tests/test_drift.py` | VERIFIED 2026-09-27 (363-run) |
| DETERMINISTIC_PREDICT (same input → same prediction) | CF-008 | `test_agent.py::test_explanation_deterministic_fallback` + deterministic artifact (`predict_risk` pure in artifact+features; sklearn LR deterministic by construction) | `tests/test_agent.py`, `backend/predict_service.py` | VERIFIED with note 2026-09-27 (no direct pin test — hardening candidate, residual risk low) |
| LATENCY_SLA (prediction within budget) | CF-022 | **none** — `scripts/bench_creditflow.py` exists, no automated gate | — | GAP → DEF-CF-001 (OPEN, P1 exception documented) |
