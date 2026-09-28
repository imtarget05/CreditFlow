# CreditFlow — Interview Evidence Kit

> CreditFlow: ML credit-risk decision engine (LogReg wins on business cost:
> cost=201, recall=0.80, threshold t=0.20). README badge: 196 tests.
> Phase-2 addition: 3 disbursement-safety tests (see root HARD_TEST_REPORT.md).

---

## 1. STAR story

**Situation.** Lending decisions were accuracy-driven — a model with good
accuracy still missed expensive defaults. Disbursement needed an auditable,
tamper-evident money trail, and borderline cases needed a human, not a silent
auto-decision.

**Task.** Build a decision engine optimised for business cost (FN=5.0 missed
default, FP=1.0 wrong rejection), with explainability, HITL review band, and a
tamper-evident disbursement ledger.

**Action.**
- Trained LogReg/Tree/Forest/XGBoost; selected **Logistic Regression** on
  business cost (201) with cost-tuned threshold **t=0.20** (approve <0.20,
  review 0.20–0.50, reject ≥0.50); 5 derived features with zero-division
  guards (DTI/LTI/debt-to-loan/employment-stability/history-ratio).
- 12-node LangGraph state machine; REVIEW band interrupts for human approval,
  pausing/resuming asynchronously.
- Disbursement ledger: SQLite, deterministic contracts `HDTD-YYYYMMDD-XXXX`,
  **SHA-256 tamper-evident hashes**, idempotency-keyed execution (see
  `docs/adr/0001-*`); LLM explains, never decides; PSI drift monitoring.

**Result.** 196-test suite; business-cost-optimal threshold shipped; REVIEW
band + idempotent disbursement covered including 3 Phase-2 safety tests.
Private on-prem deployment policy: CONFIDENTIAL data never touches public cloud.

## 2. System-design Q&A

**Q1: Why pick LogReg over XGBoost when XGBoost usually wins?**
The objective is business cost, not accuracy: missing a default costs 5× a
wrong rejection. On that metric LogReg (cost 201, recall 0.80) beat XGBoost —
simpler, calibrated, explainable to regulators. Accuracy-leaderboard thinking
would have shipped the more expensive model.

**Q2: Why idempotency keys instead of a distributed lock for disbursement?**
Money movement must survive retries: a lock serialises but does not dedupe — a
retried request after a timeout still double-pays. An idempotency-keyed ledger
makes the second execution a no-op returning the original contract. Locks also
add a failure domain (lock service down = no lending); the ledger degrades to
single-writer SQLite semantics instead.

**Q3: Why does the LLM only explain, never decide?**
Auditability: the decision must be a deterministic function of
features+threshold+policy so any auditor can replay it. An LLM in the decision
path makes outcomes non-reproducible and unexplainable to a banking regulator.
The LLM writes the memo; rules + model sign the decision.

**Q4: SPOF and scaling ceiling?**
SQLite ledger is single-writer — correct for branch-scale throughput, a ceiling
at core-banking scale. Scale path: ledger → append-only journal on Postgres
with the same hash chain + idempotency schema, decision service stateless
behind it. PSI drift loop is batch, not streaming — intraday drift is blind.

## 3. Live-demo script (5 steps)

```bash
# 1. Start backend + frontend
docker compose up --build  # API :8000, UI :5173 (see README Quick Start)
# 2. Score an application -> APPROVE with explanation + business cost
curl -s http://localhost:8000/score -H 'Content-Type: application/json' -d @docs/qa/manual-acceptance.json
# 3. Score a borderline case -> REVIEW (LangGraph interrupt, waits for human)
curl -s http://localhost:8000/decide -H 'Content-Type: application/json' -d '{"score_band":"review",...}'
# 4. Approve -> idempotent disbursement, contract HDTD-YYYYMMDD-XXXX + SHA-256 hash
curl -s http://localhost:8000/approve -H 'Content-Type: application/json' \
  -d '{"application_id":"...","idempotency_key":"..."}'
# 5. Replay step 4 verbatim -> same contract returned, no double disbursement
curl -s http://localhost:8000/approve -H 'Content-Type: application/json' \
  -d '{"application_id":"...","idempotency_key":"..."}'
```

| Step | URL | Expected |
|---|---|---|
| 2 | `POST /score` | Score + threshold band + feature explanation |
| 3 | `POST /decide` | `REVIEW` + paused workflow awaiting human |
| 4 | `POST /approve` | Contract issued, ledger hash recorded |
| 5 | `POST /approve` (replay) | Identical contract, ledger row count unchanged |
