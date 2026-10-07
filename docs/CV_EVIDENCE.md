# CV Evidence Matrix — CreditFlow

Date: 2026-10-05. Status: VERIFIED = reproducible here; PARTIAL = real but
bounded; REMOVE = do not claim.

| CV claim | Evidence (file:line / artifact) | Reproduce command | Status |
|---|---|---|---|
| API-key auth on sensitive routes (workflow state, audit, applications, disbursements, approvals) | `backend/security.py:144` `require_principal` (fail-closed, `compare_digest`); enforced on `GET /predict/graph/{thread_id}` + `GET /audit/{application_id}` (`backend/app.py`), all `/applications*`, `/disbursements*`, approve route | `python -m pytest tests/test_api_auth.py -q` (32 passed, incl. `test_graph_state_and_audit_require_api_key`) | VERIFIED |
| Replay-safe HITL approval + idempotency | `backend/app.py:approve_graph_workflow` (idempotency-key gate, single PENDING_REVIEW reservation); `pipeline/storage/ledger.py:find_idempotent_approval` | `python -m pytest tests/test_approval_idempotency.py tests/test_e2e_disbursement.py -q` | VERIFIED |
| Model artifact integrity (SHA-256) | Load-time gate `backend/predict_service.py:validate_model_bundle` (hash verify vs `manifest.json:artifacts`); standalone `models/production/SHA256SUMS` + `scripts/check_artifacts.py`; CI step `CI: Model artifact integrity` | `python scripts/check_artifacts.py --dir models/production`; `python -m pytest tests/test_check_artifacts.py tests/test_predict_units.py tests/test_models.py -q` | VERIFIED |
| Document artifact persistence (disk + SHA-256) | `pipeline/storage/ledger.py:record_document/list_documents` (`application_documents` table); records `checksum_sha256`, `file_size_bytes`, `mime_type`, `storage_path`; writes artifacts directly to `data/documents/<app_id>/`; emits `DocumentUploaded` event; `POST/GET /applications/{id}/documents` in `backend/app.py` | `python -m pytest tests/test_ml_reliability_evidence.py -k document -q` | VERIFIED |
| Tamper-evident cryptographic decision snapshots | `pipeline/storage/ledger.py:record_decision_snapshot/verify_decision_snapshot_hash` (`decision_snapshots` table); SHA-256 hash digest over decision ID, app ID, decision, model version, probability, reviewer, timestamp, and artifact checksum; verifies unchanged state | `python -m pytest tests/test_ml_reliability_evidence.py -k snapshot -q` | VERIFIED |
| Replay-safe HITL approval + idempotency | `backend/app.py:decide_application` (idempotency-key anti-double-click replay, atomic PENDING_REVIEW reservation); `pipeline/storage/ledger.py:find_idempotent_approval`; emits `ApplicationApproved`/`ApplicationRejected` | `python -m pytest tests/test_ml_reliability_evidence.py -k idempotency -q` (2 passed) | VERIFIED |
| Model artifact integrity (SHA-256) | Load-time gate `backend/predict_service.py:validate_model_bundle` (hash verify vs `manifest.json:artifacts`); standalone `models/production/SHA256SUMS` + `scripts/check_artifacts.py`; CI step `CI: Model artifact integrity` | `python scripts/check_artifacts.py --dir models/production`; `python -m pytest tests/test_check_artifacts.py tests/test_predict_units.py tests/test_models.py -q` | VERIFIED |
| PSI drift monitoring & domain events | `pipeline/monitoring/drift.py:psi/detect_drift`; reference stats `models/production/reference_stats.json`; `GET /drift` route emits `ModelDriftDetected` domain event to outbox when drift exceeds threshold | `python -m pytest tests/test_ml_reliability_evidence.py -k drift -q` | VERIFIED |
| MLflow training integration | `scripts/train_models.py:_log_mlflow` (params/metrics/artifacts, graceful skip when absent); runs gitignored (`mlruns/`, `mlflow.db`) | `MLFLOW_TRACKING_URI=sqlite:///mlflow.db python scripts/train_models.py` (needs deps + dataset) | PARTIAL — wording must stay "MLflow-integrated training workflow", not "production tracking evidence" |
| Full test suite | `tests/` — 426 passed, 0 skipped (2026-10-07, clean clone `b4b031b`, Python 3.12.13: 405 fast + 18 slow + 3 production integration; integration may skip if Render is cold or npm missing) | `python -m pytest tests/ -q` | VERIFIED |
| No committed secrets | `SECURITY.md` policy; `git ls-files` shows no `.env`/keys; key only via `CREDITFLOW_API_KEY` env; production refuses to start unset (`security.py:require_configured_key`) | `git grep -InE '(secret\|token\|password\|BEGIN .*PRIVATE KEY\|AKIA\|sk-[A-Za-z0-9]\|ghp_)' $(git ls-files)` | VERIFIED |


Known limitations (say these in interview, don't hide): `POST /predict/graph`
(intake) is intentionally unauthenticated; single shared key, no per-user
roles beyond the credit-authority matrix; SQLite ledger is the dev default
(Postgres path exists via `ledger.py:init_db_pg`).
