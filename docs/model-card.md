# CreditFlow Model Card — logistic_regression_v001

Rebuild quantitative diagnostics from the committed bundle and synthetic data
with `python scripts/build_model_card.py`. The report generator computes ROC-AUC,
PR-AUC, KS, Brier score, expected calibration error, calibration bins,
three-way decision rates, and an age-band diagnostic. The committed benchmark
snapshot currently records the established metrics below; the additional
calibration/ranking metrics are generated on demand so they are never filled
with estimates.

## Intended use

- **Purpose:** portfolio demonstration of a consumer installment-loan credit
  risk decision-support workflow using a deterministic synthetic proxy dataset.
- **Users:** reviewers and analysts exploring the demo workflow.
- **Outputs:** a default-risk probability plus APPROVE / REVIEW / REJECT routing
  under the demo threshold policy. REVIEW cases can be paused for human review.
- **Not suitable for:** real applicants, production lending, pricing,
  adverse-action notices, or autonomous credit decisions.
- **Decision owner:** deterministic model and policy rules produce the proposed
  route; authorized human reviewers own cases routed to review. LLM text never
  controls the score or decision.

## Data and model

| Attribute | Description |
|---|---|
| Dataset | `data/creditflow_dataset.csv`, 5,000 deterministic synthetic rows |
| Dataset SHA-256 | `b65728f853cad72e3ce51a639ac96c7f04e935170e79acda637b4e5eec2f5d80` |
| Split | Stratified train/validation/test, 3,500 / 750 / 750, seed 42 |
| Positive label | Synthetic default indicator |
| Model | Logistic Regression, version `logistic_regression_v001` |
| Features | Eight application fields plus five engineered financial/history ratios |
| Selection | Minimum validation business cost; FN=5 and FP=1 in hypothetical units |
| Thresholds | Saved approve/review threshold 0.20; reject threshold 0.80 |

The dataset is seeded and synthetic, with injected label signal. It does not
represent real borrower populations, lender loss, or real-world default rates.
The cost weights are relative units and are not currency amounts.

## Established held-out test snapshot

The model and threshold were selected using validation data. Test results are
reporting-only. Test confusion matrix at the binary tuned threshold is TN=558,
FP=92, FN=30, TP=70.

| Metric | Test value |
|---|---:|
| ROC-AUC | 0.8672 |
| Precision | 0.4321 |
| Recall | 0.7000 |
| F1 | 0.5344 |
| Accuracy (context only) | 0.8373 |
| Business cost (5×FN + 1×FP) | 242 hypothetical units |

PR-AUC, KS, Brier score, ECE, calibration bins, decision mix at candidate
thresholds, and age-band outcomes are calculated by the reproducible report
generator. They are not currently present in the committed benchmark snapshot.

## Explanation and fairness limits

The current model includes age as an input. The generated age-band table is a
descriptive diagnostic over synthetic data only; it is not a fairness
certification. A meaningful assessment needs approved real population data,
legal review, confidence intervals, a chosen fairness definition, and analysis
of relevant protected groups and proxies. Do not infer fairness from the
synthetic results.

The explanation layer is constrained to explanation fields, checks numeric
grounding, and falls back to deterministic text. Numeric grounding is not a
semantic faithfulness proof. Public LLMs are blocked for `CONFIDENTIAL` data by
default. LLM judge evaluation uses only checked-in synthetic fixtures and is
advisory; it cannot change a decision, model, or threshold.

## Monitoring and review policy

The `/drift` endpoint measures feature and prediction distribution PSI. It does
not measure outcome quality without delayed labels, and it does not currently
act as a production kill switch.

| Signal | Interpretation | Proposed manual action |
|---|---|---|
| PSI < 0.10 | No material shift under current heuristic | Continue routine review |
| PSI 0.10–0.20 | Warning band | Inspect affected features and cohort mix |
| PSI ≥ 0.20 | Drift detected | Pause unattended approvals, route to human review, investigate before resuming |
| Delayed-label deterioration | Not currently measured online | Backtest by cohort; require independent validation before changing model/policy |
| Calibration deterioration | Not currently measured online | Recompute Brier/ECE and bins against matured labels |

These are governance recommendations, not automated controls in the current
service. Before any real lending use, add independent validation, temporal
backtesting, documented adverse-action mapping, production identity/RBAC,
fairness review, and data-retention controls.
