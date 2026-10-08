# Test Execution Report — CreditFlow

Cases: [`TEST_CASES.md`](TEST_CASES.md) (`CF-001`→`CF-022`).
Evidence dir: [`evidence/`](evidence/). Session date: 2026-09-27 UTC.
Runner for offline rows: `/tmp/factorygen/bin/python -m pytest -q`.

| Date (UTC) | Command | Scope | Result | Verdict | Evidence |
|---|---|---|---|---|---|
| 2026-09-28 | `CreditFlow/.venv/bin/python -m pytest tests/ --ignore=tests/test_models.py --ignore=tests/test_model_update.py -q` | Full core decision & disbursement suite (368 collected) | **365 passed, 3 skipped, 0 failed** (430.97 s) | **QA READY (PASS ✅)** | [`evidence/2026-09-28-pytest-full.log`](evidence/2026-09-28-pytest-full.log) |
| 2026-09-27 | `pytest -q tests/test_ledger_property.py` | CF-018 (conservation, replay, tamper) | **3 passed** (53.39 s) | VERIFIED ✅ | `evidence/2026-09-27-safety-core.log` |
| 2026-09-27 | `pytest -q tests/test_approve_race.py tests/test_threshold_boundary.py tests/test_check_artifacts.py` | CF-005–007, CF-010–012, CF-017 | **18 passed** (6.65 s; 14 defs + 4 parametrized) | VERIFIED ✅ | same log |
| — | `pytest` (full suite, 210 defs) | CF-001→022 | UNVERIFIED — last-known: badge 196 + 3 Phase-2 disbursement-safety (HARD_TEST_REPORT.md §III) | SUPERSEDED by row below | rerun in CI |
| 2026-09-27 | `CreditFlow/.venv/bin/python -m pytest -q -rs --ignore=tests/test_models.py --ignore=tests/test_model_update.py --ignore=llm-gateway` (fresh `.venv` from `requirements.txt`) | full app suite | **363 passed, 5 skipped, 0 failed** (264.44 s) | VERIFIED ✅ | `evidence/2026-09-27-pytest-full.log` |

Excluded with cause: `test_models.py` + `test_model_update.py` (import `xgboost`, whose
`libxgboost.dylib` needs system OpenMP `libomp.dylib` — absent; `brew install libomp`
fell back to an LLVM source build, killed as impractical; sklearn-bundled libomp is
version-incompatible `___kmpc_dispatch_deinit`) → env-BLOCKED, non-gating for CF-001→022
(factory-comparison coverage only). `llm-gateway/tests` needs its own env (`No module named 'server'`).
Skips (reasoned): `test_deploy_pages.py` ×3 (npm absent exit 127; live Azure API unreachable),
`test_llm_provider.py` ×2 (vcrpy not installed).

## How to record a run

1. Run the suite (see `TEST_PLAN.md`).
2. Save raw output under `evidence/YYYY-MM-DD-<scope>.log`.
3. Fill one row above; update `Status` in `TEST_CASES.md`.
4. Any FAIL/FLAKY gets an entry in `DEFECT_REPORT.md` before the run counts as reviewed.
