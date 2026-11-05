# CreditFlow enterprise readiness — 2026-11-03

**Verdict: NOT READY FOR SACOMBANK PRODUCTION.** This is an engineering baseline, not a Sacombank or regulatory certification. The repository has no approved bank data, IdP tenant, integration sandbox, internal policy set, or formal acceptance owners. The public portfolio deployment is a separate synthetic demo.

## Implemented and locally verifiable

| ID | Control | Current evidence |
|---|---|---|
| SEC-01 | No built-in operator accounts or signing secret | `backend/security.py`; `tests/test_security_baseline.py`; demo login requires explicit non-production configuration |
| SEC-02 | Applicant workflow requires identity | `/predict/graph` is protected except explicit non-production demo opt-in; production human routes require OIDC |
| SEC-03 | Production separates human and machine identity | RS256 OIDC verification with issuer, audience, JWKS and group mapping for human routes; API key only for scheduled monitoring/reminder endpoints; `tests/test_oidc_identity.py` |
| SEC-04 | Runtime user revocation blocks bearer tokens | Account revocation updates the identity set checked by OIDC and demo JWT authentication, records session revocation, and clears the identity on reactivation; `tests/test_user_management_api.py`. State is in-process and is not a production persistence integration. |
| SEC-05 | Production application reads are scoped to the staff queue | Application list/detail, workflow state, audit, documents, scoring and explanation check visible work items; demo ledger read is disabled in production; `tests/test_application_scope.py` |
| PRIV-01 | Browser does not persist applicant profiles | `frontend/src/App.jsx`; `tests/test_frontend_privacy.py` |
| WF-01 | Review task creation and controlled actions | `pipeline/storage/work_items.py`; `/api/work-items`; idempotent claim, manager assignment, supplement request/resume, escalation and completion with actor/reason; completion atomically creates the next document → underwriting → Risk-review task, without making a credit decision; business-calendar deadlines and hourly reminder outbox; `tests/test_work_items.py` |
| WF-05 | Business-calendar deadline calculations | Timezone-aware SLA computation, workday/hour boundaries, holidays, configured and default calendars; `tests/test_business_calendar.py` |
| WF-02 | Missing-document metadata check | Configured type checklist returns `MISSING`, `REVIEW_REQUIRED` or `UNCONFIGURED`; it never claims content or authenticity is verified; `tests/test_document_checklist.py` |
| WF-03 | Repeated profile review signal | Exact scoring-feature matches flag prior case IDs and open a Risk exception task in the synthetic workflow; this cannot prove customer identity or justify automatic merge; `tests/test_duplicate_profiles.py` |
| WF-04 | Staff pilot queue and in-app reminders | Demo login session stays in tab memory; role queue supports claim, manager assignment, escalation, completion, and overdue reminders scoped to current owner; frontend build and `tests/test_work_items.py` |
| MON-01 | One source of truth for drift | Airflow calls `/api/monitoring/check`; API uses the serving drift calculation and creates one risk task per run; no independent score or `COMPLIANT` label |
| MON-02 | Loan outcome tracking and portfolio metrics | Outcome upsert, partial updates, filtering, outstanding-loan selection, and cohort/overall aggregates; `tests/test_outcome_tracking.py` |
| SAFE-01 | Demo decision path is closed in production | Production readiness returns 503; scoring, workflow start, approval, document upload and settlement return stable 503 errors; both banking sandbox adapters refuse simulated transfers in production |

## Open acceptance gates

| Priority | Gate | Required evidence / owner |
|---|---|---|
| P0 | Bank IdP and user lifecycle | Sacombank IAM supplies issuer, audience, JWKS, group IDs, revocation/session policy; integration tests for login, revocation, role changes and per-record access. Local queue-scoping tests and generic token validation do not close this gate. |
| P0 | Real-data rights and independent model validation | Data owner approves lawful dataset; Risk validates temporal backtest, calibration, subgroup errors, policy reasons and thresholds. Synthetic metrics cannot authorize lending use. |
| P0 | Decision and core banking contract | Business owner and core banking team approve request/response, authority matrix, dual control, reconciliation and rollback. Current demo approval and ledger are not a real payment rail. |
| P0 | Privacy and security review | Data inventory, retention/deletion, encryption and backup policy, threat model, independent penetration test and remediation. Map applicable internal controls and law with Legal/InfoSec. |
| P0 | Disaster recovery | Approved PostgreSQL/document storage deployment, encrypted backups, restore drill, RPO/RTO and failover evidence. Current local SQLite/file storage is a demo mode. |
| P1 | Complete daily staff workflow | Bank-approved document policy/content verification, authoritative customer identifiers for duplicate detection, directory-backed assignment/authority matrix, approved calendar, external notification channel, UI with IdP session, and UAT with loan officers and approvers. The current pilot uses a configured demo staff map and in-app reminders; no bank calendar, directory or delivery channel is connected. |
| P1 | Portfolio monitoring with delayed outcomes | Governed outcome feed, cohort metrics, calibrated alerts, model-change approvals and Risk/MLOps runbook. Current monitor reports distribution drift only. |
| P1 | Operational quality bar | CI full suite, load/concurrency tests, accessibility, dependency and secret scanning, traceability, deployment smoke, rollback drill, signed release decision. |

## Local QA evidence

Environment: macOS development checkout, Python 3.11.15 in the project `.venv`, synthetic fixtures only. No bank service or customer data was used.

| Check | Command | Result |
|---|---|---|
| Full pytest suite | `.venv/bin/pytest -q` | Re-run on current checkout: **608 passed, 3 skipped, 0 failed** in 552.69s; repeat after MLflow 3.17.2: **608 passed, 3 skipped, 0 failed** in 943.77s. Working tree contains uncommitted changes; this is not a release SHA. |
| Tier A focused regression | `.venv/bin/pytest -q tests/test_user_revocation.py tests/test_user_management_api.py tests/test_outcome_tracking.py tests/test_business_calendar.py` | 41 passed. Covers token and session revocation, user lifecycle/session routes, outcome tracking, and business calendar. |
| Ruff on new tests and user-management module | `/opt/homebrew/bin/ruff check tests/test_user_revocation.py tests/test_user_management_api.py tests/test_outcome_tracking.py tests/test_business_calendar.py backend/user_management.py` | Passed. |
| Repository Ruff check | `/opt/homebrew/bin/ruff check .` | Re-run with Ruff 0.16.6: **201 findings** across the repository. Still not clean; the earlier 568 count is superseded. |
| Gate 8A model quality | `.venv/bin/python gate8a_model_quality.py` | 13/13 checks passed; negative-candidate rejection and approved-candidate acceptance were both observed. |
| Reproducible training run | `docs/evidence/training-run-20261103-171501.txt` | Captures model selection, environment versions, dataset hash and the training command; synthetic-data evidence only. |
| Frontend build | `npm run build` in `frontend/` | Passed. This is a compile check, not staff UAT or accessibility acceptance. |
| Python dependency audit | `uvx pip-audit -r requirements.txt --format columns` | The re-run found three advisories on `mlflow==3.16.0`; after pinning `mlflow==3.17.2`, the same audit reports **no known vulnerabilities**. MLflow 3.17.2 import and the 11 model/model-update tests pass; a new tracking-server end-to-end run is still open. |
| Frontend runtime dependency audit | `npm audit --omit=dev --audit-level=high --json` in `frontend/` | Zero reported vulnerabilities. |
| Diff hygiene and CI syntax | `git diff --check`; `yaml.safe_load` of `.github/workflows/ci.yml` | Passed. |

The suite is local engineering evidence only. The production boundary changes are intentionally fail-closed: a release must replace these blocks with reviewed bank integrations and approved model artifacts, then rerun the complete acceptance matrix in Sacombank's environment.

## Release decision rule

No production go-live while any P0 gate is open. Each gate needs an owner, dated evidence, reviewed exceptions and a recorded approval from business, Risk, InfoSec and operations. This report should be updated from actual test and deployment output; historical badges or local synthetic tests do not count as production acceptance.

References for the control mapping: [OWASP ASVS 5.0](https://asvs.owasp.org/), [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework), and the [Vietnamese legal text portal](https://vbpl.vn/). Sacombank Legal and InfoSec determine which obligations apply.
