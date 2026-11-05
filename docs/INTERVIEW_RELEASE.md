# CreditFlow interview release

## One-sentence pitch

CreditFlow is a synthetic credit-risk decision engine that combines deterministic
ML scoring with protected human review, maker-checker approval, audit snapshots,
and a core-banking adapter boundary.

## Five-minute demo

Run from the repository root:

```bash
.venv/bin/python -m pytest -q tests/test_mock_oidc_idp.py \
  tests/test_core_banking_adapter.py tests/test_backup_restore_drill.py \
  tests/test_model_validation.py
```

The focused production-gate run currently passes **19 tests with 1 skipped**.
The full project environment run passes **620 tests with 4 skipped**.

## Interview talking points

- Human routes use RS256 OIDC verification and group-to-role mapping; machine
  monitoring uses a separate API-key boundary.
- Approval and disbursement boundaries are idempotent and auditable.
- The adapter models maker-checker, transaction lifecycle, reconciliation,
  rollback, and VietQR/NAPAS integration points without moving real money.
- Model validation includes temporal backtesting, calibration/ECE, and subgroup
  analysis.

## Honest production boundary

- The dataset and public frontend are synthetic demos.
- No bank IdP, real lending data, payment rail, or regulatory approval is
  connected.
- The Kubernetes image reference is prepared, but AKS sync/apply was not run
  from this machine because the cluster is private-link/VNet-only.

## Evidence

- [Enterprise readiness report](qa/ENTERPRISE_READINESS_2026-11-03.md)
- [Kubernetes API manifest](../k8s/20-creditflow-api.yaml)
- [OIDC demo](../scripts/mock_oidc_idp.py)
- [Model validation](../scripts/model_validation.py)
