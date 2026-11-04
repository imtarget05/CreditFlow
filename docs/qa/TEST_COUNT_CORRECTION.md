# Test count correction — CreditFlow

## Correct test counts

> **Latest working-tree recheck (2026-11-03):** `.venv/bin/python -m pytest -q`
> produced **608 passed, 3 skipped, 0 failed**. This supersedes the earlier
> 605/3 local run below; both are historical local runs and neither certifies
> bank production readiness.

The prior full-suite result was **605 passed, 3 skipped, 0 failed** in 332.44s.

Source: `docs/qa/ENTERPRISE_READINESS_2026-11-03.md:43` — prior local run; latest
working-tree output is recorded above.

## Ruff result and toolchain caveat

The latest user-toolchain run reported **570 Ruff findings**. The readiness
report's earlier **201 findings** was produced by Ruff 0.16.6 on a different
checkout/tool invocation. Until CI's pinned Ruff version is run on the same
release SHA, report the finding count together with its version and do not call
the repository lint-clean.

## The claimed "558 PASS / 2 FAIL / 5 SKIP" is unsupported

The table entry "558 PASS, 2 FAIL, 5 SKIP (99.64%)" does **not** match any document in this repository. No file records 2 failures. Every documented test run shows 0 failed.

## All documented test results with sources

| Result | Source | Scope / notes |
|---|---|---|
| 605 passed, 3 skipped, 0 failed (332.44s) | `docs/qa/ENTERPRISE_READINESS_2026-11-03.md:43` | Full pytest suite, `.venv/bin/pytest -q`; current local QA evidence |
| 41 passed | `docs/qa/ENTERPRISE_READINESS_2026-11-03.md:44` | Tier A focused regression (4 named test files) |
| 365 passed, 3 skipped, 0 failed (430.97s) | `docs/testing/TEST_EXECUTION_REPORT.md:9` | 2026-09-28 full core decision & disbursement suite (368 collected) |
| 363 passed, 5 skipped, 0 failed (264.44s) | `docs/testing/TEST_EXECUTION_REPORT.md:13` | 2026-09-27 full app suite (fresh `.venv`) |
| 3 passed | `docs/testing/TEST_EXECUTION_REPORT.md:10` | `test_ledger_property.py` (CF-018) |
| 18 passed | `docs/testing/TEST_EXECUTION_REPORT.md:11` | CF-005–007, CF-010–012, CF-017 |
| 426 passed, 0 failed, 0 skipped | `README.md:260-262` | Historical baseline (2026-10-07, clean clone b4b031b); NOT current evidence |
| 57 passed | `docs/qa/final-manual-acceptance.md` | Automated regression |

None of these show any FAILs. The claimed "2 FAIL" is not supported by any document.

## Production readiness

Production readiness is **NOT READY FOR SACOMBANK PRODUCTION** (5 P0 gates open).

Source: `docs/qa/ENTERPRISE_READINESS_2026-11-03.md:1-3` — "Verdict: NOT READY FOR SACOMBANK PRODUCTION." The report lists 5 P0 open gates (Bank IdP and user lifecycle; Real-data rights and independent model validation; Decision and core banking contract; Privacy and security review; Disaster recovery).

No document in this repo claims an "8.0 / 10" production readiness score. A repository-wide search for `8.0/10` found no matches.

## Additional verification notes (accuracy, not code changes)

- Clean Architecture 4 layers: not documented anywhere — UNVERIFIABLE.
- Process lock: only intra-process `threading.Lock()` found (`backend/security.py:233-234` in-memory revocation sets); not a cross-process lock.
- Database Atomic CAS: only CAS-adjacent conditional SQL UPDATE patterns found, not formal atomic CAS.
- XGBoost: present in `requirements.txt`, but production serving model is Logistic Regression (selected by lowest business cost). See `README.md:50-52`.
- Disbursement `execute()` is a stub — no real Core Banking integration. See `README.md:90-96`.
- Sacombank IdP is not connected — production approval returns `PRODUCTION_APPROVAL_NOT_INTEGRATED`.

## JWT `sub` claim finding (`backend/security.py`)

- `create_access_token` (`backend/security.py:98-116`) sets `"sub": identity` where `identity` is the caller-supplied string.
- It is invoked at `backend/app.py:568` with `user["username"]`, so `sub` is the username string in demo JWTs.
- `validate_oidc_token` (`backend/security.py:158-195`) requires `sub` via `options={"require": [..., "sub", ...]}` (`security.py:179`) and uses `claims["sub"]` as the `Principal.identity` (`security.py:192`).
- `decode_access_token` (`security.py:118-135`) and `require_principal` (`security.py:374-379`) read `payload["sub"]` for identity and revocation checks.

Finding: `sub` is used consistently as a string identity throughout `backend/security.py`. No UUID-vs-string `sub` claim mismatch (of the kind seen in QuanLyKho) is present in CreditFlow. No production source code modification is needed.
