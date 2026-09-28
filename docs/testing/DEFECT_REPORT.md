# Defect Report — CreditFlow

| ID | Severity | Title | Repro | Evidence | Status |
|---|---|---|---|---|---|
| _(none this session)_ | — | Safety core 21/21 green (`test_ledger_property` 3 + race/boundary/artifacts 18); no failures observed | 2026-09-27 runs in `TEST_EXECUTION_REPORT.md` | `evidence/2026-09-27-safety-core.log` | — |
| DEF-CF-001 | P1 (coverage gap) | No automated prediction-latency gate (CF-022). `scripts/bench_creditflow.py` exists but no test asserts an SLA, so latency regressions are undetected | full suite 2026-09-27: zero timing assertions in `tests/` (`test_predict_units.py` has none) | `evidence/2026-09-27-pytest-full.log` + `TEST_CASES.md` CF-022 | OPEN (P1 exception documented; gate conditional) |
| ENV-CF-001 | env-BLOCKED (non-gating) | `test_models.py` + `test_model_update.py` cannot collect: `xgboost` native lib needs system OpenMP (`libomp.dylib` absent); brew fell back to LLVM source build (killed); sklearn-bundled libomp is ABI-incompatible | `import xgboost` → `XGBoostError: libxgboost.dylib could not be loaded` | shell transcript 2026-09-27 | BLOCKED with cause (factory-comparison coverage only; CF-001→022 unaffected) |

Historical defects with on-disk evidence in this repo: **none verified** — none recorded
(nothing invented).

## Lifecycle

`OPEN → FIXED` (with re-test evidence) or `OPEN → MITIGATED` (workaround +
root-cause tracking ID) or `→ WONTFIX` (justification required for P0/P1).
Every defect links the failing case ID from `TEST_CASES.md`.
