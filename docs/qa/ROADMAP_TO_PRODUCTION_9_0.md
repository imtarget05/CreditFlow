# CreditFlow — Roadmap to Production Readiness > 9.0/10

**Scope:** CreditFlow production release targeting Sacombank.
**Baseline:** `docs/qa/ENTERPRISE_READINESS_2026-11-03.md` — Verdict: NOT READY FOR SACOMBANK PRODUCTION. 5 P0 gates open.
**Current local QA:** ~608 passed, 3 skipped, 0 failed (full pytest suite); production boundary is fail-closed (returns 503 for scoring, workflow start, approval, document upload, settlement).

**Release decision rule (from ENTERPRISE_READINESS.md:56):** No production go-live while any P0 gate is open. Each gate needs an owner, dated evidence, reviewed exceptions, and recorded approval from business, Risk, InfoSec, and operations. Local synthetic tests do not count as production acceptance.

---

## 1. P0 Gate Closure Plan (5 gates)

### Gate 1 — Bank IdP and user lifecycle (Sacombank IAM)
**Status:** Open. Sacombank IdP not connected; returns `PRODUCTION_APPROVAL_NOT_INTEGRATED`. Current OIDC verification (issuer, audience, JWKS, group mapping) is generic/local; revocation state is in-process only.

**Evidence needed:**
- Sacombank IAM tenant configured; issuer URL, audience, JWKS URL, and group IDs documented and approved by Sacombank IAM.
- OIDC provider registered against Sacombank issuer; token validation against real JWKS.
- Group-to-role mapping approved by Sacombank (group ID → CreditFlow role).
- Integration tests for Sacombank IdP login, token revocation, role change, and per-record access (against sandbox/tenant).
- Revocation blocks bearer tokens end-to-end; reactivation clears revocation.

**Owner:** Platform engineering + Sacombank IAM contact.
**Estimated effort:** Medium–High (depends on Sacombank tenant provisioning turnaround).
**Blocking dependencies:** Sacombank IAM tenant and credentials (External Dependency #1); completion of Gate 5 (persistent revocation state) for production-grade session lifecycle.

### Gate 2 — Real-data rights and independent model validation
**Status:** Open. Production model is Logistic Regression (XGBoost is a dependency but not used). All metrics are on synthetic fixtures only; no lawful dataset approved by the data owner.

**Evidence needed:**
- Data owner written approval for the lawful dataset (scope, provenance, retention, deletion).
- Independent model validation pipeline: temporal backtest (time-labeled intake per `docs/qa/temporal-validation-intake.md`), calibration report, subgroup error analysis, policy-reason and threshold review.
- Risk sign-off that synthetic metrics cannot authorize lending use; validation performed by a reviewer outside the model development group.
- Model artifact selected, versioned, and frozen for production (Logistic Regression or justified alternative).

**Owner:** ML engineering + independent Risk reviewer + data owner.
**Estimated effort:** Medium–High (requires data rights approval before validation can run).
**Blocking dependencies:** Legal approval for real data usage (External Dependency #3); independent reviewer outside model development group.

### Gate 3 — Decision and core banking contract
**Status:** Open. Disbursement `execute()` is a stub — no real Core Banking integration. Demo approval and ledger are not a real payment rail.

**Evidence needed:**
- Business owner and core banking team written approval of request/response contract, authority matrix, dual control, reconciliation, and rollback.
- Real Core Banking integration adapter replacing the disbursement stub (sandbox → production).
- End-to-end tests for the core banking disbursement flow (success, failure, idempotency, reconciliation, rollback).
- Dual-control and reconciliation evidence; rollback drill performed.

**Owner:** Backend engineering + core banking team + business owner.
**Estimated effort:** High (real payment rail integration).
**Blocking dependencies:** Core banking sandbox access (External Dependency #2); Gate 1 (identity/authorization for disbursement initiation).

### Gate 4 — Privacy and security review
**Status:** Open. No data inventory, retention/deletion, encryption-and-backup policy, threat model, or independent penetration test.

**Evidence needed:**
- Data inventory (what applicant/loan data is collected, stored, processed, shared).
- Retention and deletion policy mapped to applicable law (with Legal/InfoSec).
- Encryption at rest and in transit policy (including encrypted PostgreSQL — see Gate 5).
- Threat model reviewed by InfoSec.
- Independent penetration test executed; findings remediated or accepted with dated exceptions.
- Internal control mapping and legal applicability determined by Sacombank Legal and InfoSec.

**Owner:** InfoSec + Legal + platform engineering.
**Estimated effort:** High (penetration test scheduling and remediation).
**Blocking dependencies:** Independent Risk/InfoSec review and penetration test (External Dependency #4).

### Gate 5 — Disaster recovery
**Status:** Open. SQLite/local file storage is demo mode only. No encrypted PostgreSQL, encrypted backups, restore drill, or documented RPO/RTO.

**Evidence needed:**
- Approved PostgreSQL/document storage deployment (kind-local-platform in demo; production target per architecture).
- Encrypted at-rest storage and encrypted backups.
- Restore drill executed; documented RPO and RTO targets met.
- Failover evidence; backup restore drill tests automated.

**Owner:** Platform engineering + operations.
**Estimated effort:** Medium–High.
**Blocking dependencies:** Gate 4 (encryption/backup policy).

---

## 2. Code Changes Required

| # | Change | Rationale | Gate |
|---|--------|-----------|------|
| 1 | Replace disbursement stub with real Core Banking integration adapter | `execute()` is currently a stub with no Core Banking integration | 3 |
| 2 | Connect Sacombank OIDC IdP (issuer, audience, JWKS URL, group-to-role mapping) | IdP not connected; returns `PRODUCTION_APPROVAL_NOT_INTEGRATED` | 1 |
| 3 | Implement encrypted PostgreSQL storage (replace SQLite/local file demo mode) | Local storage is demo mode; needed for DR and persistence | 4, 5 |
| 4 | Implement backup/restore with RPO/RTO targets | No encrypted backups or restore drill today | 5 |
| 5 | Implement independent model validation pipeline (temporal backtest, calibration, subgroup analysis) | Metrics are synthetic-only; Risk validation required | 2 |

**Constraint:** Do not mutate Azure/production; `kind-local-platform` is the only target for local validation. Existing localhost services stay running.

---

## 3. Test Evidence Required

| # | Test area | Requirement | Gate |
|---|-----------|-------------|------|
| 1 | Full pytest suite | Must pass with 0 failures (baseline ~608P/3S/0F — re-run on clean checkout) | All |
| 2 | Sacombank IdP login | Integration test: OIDC login against Sacombank tenant/sandbox | 1 |
| 3 | Sacombank IdP revocation | Integration test: token/session revocation blocks bearer tokens | 1 |
| 4 | Sacombank IdP role changes | Integration test: group-to-role mapping changes reflected in access | 1 |
| 5 | Core banking disbursement E2E | End-to-end test: success, failure, idempotency, reconciliation, rollback | 3 |
| 6 | Disaster recovery restore drill | Automated restore drill test; documented RPO/RTO met | 5 |

All test evidence must be produced against reviewed bank integrations and approved model artifacts, re-run in Sacombank's environment — local synthetic tests do not count as production acceptance.

---

## 4. External Dependencies (cannot be resolved by code alone)

| # | Dependency | Required from |
|---|-----------|---------------|
| 1 | Sacombank IAM tenant and credentials (issuer, audience, JWKS URL, group IDs) | Sacombank IAM |
| 2 | Core banking sandbox access | Core banking team |
| 3 | Legal approval for real data usage (lawful dataset, retention, deletion) | Sacombank Legal |
| 4 | Independent Risk/InfoSec review and penetration test | Independent Risk / InfoSec |

These are blocking dependencies for Gates 1–5 and cannot be satisfied by source-code changes alone.

---

## 5. Success Criteria for > 9.0/10

- [ ] All 5 P0 gates closed, each with owner and dated evidence.
- [ ] Sacombank production approval integrated — not `PRODUCTION_APPROVAL_NOT_INTEGRATED`.
- [ ] Real Core Banking integration in place — disbursement stub removed.
- [ ] Encrypted PostgreSQL storage replacing SQLite/local file demo mode.
- [ ] Encrypted backup/restore implemented with documented RPO/RTO; restore drill passed.
- [ ] Independent model validation pipeline complete: temporal backtest, calibration, subgroup analysis; Risk sign-off.
- [ ] Full pytest suite passes with 0 failures plus new IdP, disbursement E2E, and DR restore-drill tests.
- [ ] Independent penetration test executed; findings remediated or formally accepted with dated exceptions.
- [ ] Recorded production go-live approval from business, Risk, InfoSec, and operations.
- [ ] No production go-live while any P0 gate remains open.

**Note:** This roadmap is an engineering plan, not a Sacombank or regulatory certification. The public portfolio deployment is a separate synthetic demo and is out of scope.
