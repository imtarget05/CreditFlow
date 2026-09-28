# Business Impact — CreditFlow (ML Credit Risk Decision Engine)

Domain facts: lending decision pipeline (validate → features → LogReg model,
cost-aware threshold t=0.20, 12-node LangGraph with human approval for REVIEW,
SQLite ledger with `HDTD-YYYYMMDD-XXXX` contracts + SHA-256 tamper evidence).
See `README.md`, `docs/spec.md`.

## Problem (cost of status quo)

Manual underwriting takes ~3 days per file, triage is inconsistent across
officers, and missed defaults (false negatives, cost 5.0) dominate wrong
rejections (false positives, cost 1.0). Money movement without an auditable
ledger is a compliance risk.

## Solution (what the system does)

LogReg (business-cost selected) scores applications; threshold engine routes
APPROVE/REVIEW/REJECT; REVIEW pauses on LangGraph interrupt for human approval;
approval writes exactly one `disbursements` row (idempotency key +
`UNIQUE(application_id)`); PSI drift monitoring watches stability.

## Impact

| Metric | Before | After | How measured |
|---|---|---|---|
| Auto-triage share | 0% | 70% | ESTIMATE — plan target; pending production workflow counts. |
| Decision latency (straight-through) | 3 days | 5 min | ESTIMATE — pending timed workflow runs; not measured here. |
| False-positive rate | — | <2% | ESTIMATE — pending held-out eval at t=0.20; current evidence is `models/production/*` benchmark only. |
| Ledger posting micro-op (200 iters, local) | — | mean 10.29 ms, p95 30.04 ms | MEASURED by `scripts/bench_creditflow.py` (mode `pipeline.storage.ledger`, temp DB via `CREDITFLOW_LEDGER_DB`), this machine 2026-09-27. Machine-dependent. |

No other number in this file is a production measurement.

## Guardrails / SLO links

- Replay-safe approval (one file = one money row); versioned model bundle
  (`manifest.json` SHA-256, startup 503 on mismatch); CONFIDENTIAL policy guard.
- SLOs: `observability/slo.yaml` (gateway availability/latency shared infra).
- Live metrics: `GET /metrics`, `GET /drift` on the CreditFlow API.

## Reproduce

```bash
cd CreditFlow
python3 scripts/bench_creditflow.py
python -m pytest tests/ -v
```
