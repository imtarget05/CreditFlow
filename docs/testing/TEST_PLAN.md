# Test Plan — CreditFlow ML Risk Decision Engine

**Contract:** [`docs/qa/QA_ACCEPTANCE.md`](../../docs/qa/QA_ACCEPTANCE.md).
**Case IDs:** `CF-001` → `CF-022` in `TEST_CASES.md`.
**Runner:** `pytest`. On disk: 27 files, **210 `def test`** under `tests/` (enumerated 2026-09-27; map first).

## Scope

Feature engineering (incl. division-by-zero safety), threshold `0.20` boundary
behavior, determinism, artifact integrity (hash), 12-node LangGraph workflow,
HITL approve/reject, disbursement idempotency, drift monitoring, latency SLA.

## Levels

| Level | What | Where |
|---|---|---|
| Unit | feature vector math, boundary scores 0.1999/0.2000/0.2001, determinism, column-reorder safety | `test_threshold.py` (11), `test_threshold_boundary.py` (5), `test_predict_units.py` (11) |
| Contract | API 422 matrix, health/ready, artifact manifest | `test_api.py` (13), `test_check_artifacts.py` (6) |
| Property | double-entry conservation, replay-never-duplicates, tamper-evident ledger | `test_ledger_property.py` (3) |
| Race | concurrent same-application approve → exactly 1 disbursement; sequential replay | `test_approve_race.py` (3) |
| Adversarial | tampered artifact hash (deploy blocked), missing artifact (no silent fallback), LLM-output injection dropped | `test_check_artifacts.py`, `test_llm_provider.py` (17) |
| Live | PG-backed ledger routing (`test_ledger_pg_routing.py`), needs live Postgres | CI/Docker only |
| Performance | prediction latency vs SLA (`scripts/bench_creditflow.py`); drift WARNING vs ALERT | NOT A GATE (single local runs) |

## Environments

| Env | Command | Scope |
|---|---|---|
| Offline | `/tmp/factorygen/bin/python -m pytest -q tests/test_ledger_property.py` (3 passed) + `tests/test_approve_race.py tests/test_threshold_boundary.py tests/test_check_artifacts.py` (**18 passed**) | dep-light safety core, 2026-09-27 |
| CI | `pytest` after requirements install | full 210-test suite (last-known: badge 196 + 3 Phase-2, UNVERIFIED here) |
| Live-infra | PG container | `test_ledger_pg_routing.py`, `test_e2e_disbursement.py` |

## Entry / exit criteria

- Entry: model artifact + hash pinned; threshold `0.20` pinned.
- Exit: P0 100% PASS incl. `ONE APPROVAL -> AT MOST ONE DISBURSEMENT` at unit + integration.

## Invariant under test

```text
ONE LOAN APPROVAL -> AT MOST ONE DISBURSEMENT
```
