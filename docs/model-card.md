# CreditFlow Model Card — logistic_regression_v001

Generated from the committed model bundle and synthetic dataset. Rebuild with
`python scripts/build_model_card.py`. This is a portfolio demonstration, not a
validation approval for lending use.

## Intended use

- **Use:** demonstrate a credit-risk decision-support workflow for a fictional
  consumer installment-loan application. The output may route a synthetic case
  to APPROVE, REVIEW, or REJECT under the demo thresholds.
- **Users:** portfolio reviewers and credit-risk analysts inspecting the demo.
- **Out of scope:** real applicants, production lending decisions, adverse-action
  notices, pricing, regulatory compliance certification, or autonomous approval.
- **Human control:** REVIEW is routed to the existing LangGraph human-approval
  interrupt. Model scores and LLM explanations do not replace reviewer judgment.

## Model and data

| Field | Value |
|---|---|
| Model/version | `logistic_regression` / `logistic_regression_v001` |
| Training data | `data/creditflow_dataset.csv`, deterministic synthetic proxy |
| Dataset SHA-256 | `b65728f853cad72e3ce51a639ac96c7f04e935170e79acda637b4e5eec2f5d80` |
| Rows / split | 5000 / stratified train-validation-test, seed 42 |
| Test rows | 750 |
| Positive label | Synthetic default indicator |
| Features | `income, age, employment_years, loan_amount, loan_term, existing_debt, credit_history, previous_defaults, debt_to_income, loan_to_income, debt_to_loan, employment_stability, credit_history_year_ratio` |
| Probability threshold | 0.20 approve cutoff; 0.80 reject cutoff; intermediate cases go to review |
| Selection objective | Validation business cost with FN cost 5 and FP cost 1 |

The cost weights are relative demo units, not monetary loss estimates. The data
generator injects signal and does not establish real-world default relationships.
Age is an input feature, so the age-band table below is a descriptive proxy-data
diagnostic, not a legally meaningful fairness assessment. No fairness guarantee
can be inferred from these results.

## Held-out test performance

The threshold was selected on validation data; this table reports the untouched
test split. Accuracy is included only as context.

| Metric | Value | Interpretation |
|---|---:|---|
| ROC-AUC | 0.8672 | Ranking discrimination |
| PR-AUC / Average Precision | 0.5152 | Positive-class precision/recall ranking |
| KS | 0.6108 | Maximum separation of default/non-default score distributions |
| Brier score | 0.0863 | Lower is better for probability accuracy |
| Expected calibration error (10 bins) | 0.0286 | Lower is better; binning-sensitive diagnostic |
| Precision at tuned threshold | 0.4321 | Default precision using the saved threshold |
| Recall at tuned threshold | 0.7000 | Default recall using the saved threshold |
| F1 at tuned threshold | 0.5344 | Precision/recall harmonic mean |
| Accuracy | 0.8373 | Context only; not the selection objective |

### Calibration bins

Equal-width bins; empty bins are omitted. ECE and Brier score are diagnostics,
not proof of calibration.

| Predicted probability | Cases | Mean predicted | Observed default rate |
|---|---:|---:|---:|
| 0.0–0.1 | 512 | 0.029 | 0.031 |
| 0.1–0.2 | 76 | 0.144 | 0.184 |
| 0.2–0.3 | 53 | 0.256 | 0.264 |
| 0.3–0.4 | 27 | 0.358 | 0.519 |
| 0.4–0.5 | 20 | 0.452 | 0.450 |
| 0.5–0.6 | 22 | 0.557 | 0.273 |
| 0.6–0.7 | 15 | 0.642 | 0.733 |
| 0.7–0.8 | 12 | 0.746 | 0.667 |
| 0.8–0.9 | 9 | 0.845 | 0.444 |
| 0.9–1.0 | 4 | 0.950 | 1.000 |

### Decision mix by candidate approve threshold

Reject cutoff remains 0.80, matching the current decision policy. Rates are
computed on the synthetic held-out test set; they are not approval targets.

| Approve cutoff | APPROVE rate | REVIEW rate | REJECT rate | Default rate among approved | Default capture in REJECT |
|---:|---:|---:|---:|---:|---:|
| 0.15 | 0.739 | 0.244 | 0.017 | 0.040 | 0.080
| 0.20 | 0.784 | 0.199 | 0.017 | 0.051 | 0.080
| 0.25 | 0.813 | 0.169 | 0.017 | 0.057 | 0.080
| 0.30 | 0.855 | 0.128 | 0.017 | 0.069 | 0.080

### Age-band diagnostic

| Age band | Cases | Observed default prevalence | APPROVE rate | REVIEW rate | REJECT rate |
|---|---:|---:|---:|---:|---:|
| 18–29 | 137 | 0.336 | 0.314 | 0.628 | 0.058 |
| 30–44 | 204 | 0.167 | 0.750 | 0.235 | 0.015 |
| 45–59 | 175 | 0.086 | 0.920 | 0.069 | 0.011 |
| 60+ | 234 | 0.021 | 0.987 | 0.013 | 0.000 |

Small synthetic group counts and constructed labels make this unsuitable for
fair-lending conclusions. A real assessment requires approved sensitive-group
data, legal review, confidence intervals, and a documented fairness methodology.

## Explanation and GenAI controls

- The domain workflow already has Underwriting Risk, Fraud & Compliance,
  Explainability, and Disbursement Execution subagents.
- The ML model, deterministic policy rules, and authorized human reviewer own
  the decision. LLM output is restricted to explanation fields and is subject
  to numeric-grounding and provider privacy controls.
- Public LLM providers are blocked for `CONFIDENTIAL` data by default. Any
  optional judge evaluation uses only fixed synthetic examples and must be
  explicitly enabled; judge scores are review signals, never approval gates.
- LLM-judge assessments are model-based and can be wrong. Pair them with
  deterministic output checks and human review; do not report them as measured
  credit-model performance.

## Monitoring and governance

Current `/drift` monitoring reports feature and score-distribution PSI only; it
does not detect performance decay without delayed ground-truth labels.

| Signal | Current interpretation | Proposed operational response (manual policy; not an automated kill switch) |
|---|---|---|
| PSI < 0.10 | No material distribution shift under the current heuristic | Continue routine review |
| PSI 0.10–0.20 | Warning band | Inspect affected features and application mix |
| PSI ≥ 0.20 | Drift detected by current implementation | Suspend unattended use, route cases to human review, investigate and document before resuming |
| Delayed-label metrics worsen | Not currently measured by `/drift` | Backtest by application cohort; require independent review before model/policy change |
| Calibration degrades | Not monitored online | Recompute Brier/ECE and calibration bins when labels mature |

Before any real lending use: independent validation, temporal backtesting,
approved fairness testing, documented adverse-action reason mapping, identity
and role-based approval controls, and production data/retention review are
required. None is established by this synthetic demo.
