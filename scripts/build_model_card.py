"""Build a reproducible model card from the committed synthetic proxy dataset."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.feature_engineering.features import add_derived_features
from pipeline.modeling.evaluate import (
    calibration_bins,
    compute_metrics,
    threshold_operating_points,
)
from pipeline.modeling.threshold import DEFAULT_REVIEW_MAX
from pipeline.modeling.train import stratified_train_val_test_split


def build_model_card() -> str:
    dataset = pd.read_csv(ROOT / "data/creditflow_dataset.csv")
    featured, _ = add_derived_features(dataset.copy())
    _, _, x_test, _, _, y_test = stratified_train_val_test_split(featured)
    bundle = joblib.load(ROOT / "models/production/pipeline.joblib")
    meta = json.loads((ROOT / "models/production/meta.json").read_text())
    probability = bundle.predict_proba(x_test)[:, 1]
    threshold = float(meta["threshold"])
    predicted = (probability >= threshold).astype(int)
    metrics = compute_metrics(y_test, predicted, probability)
    points = threshold_operating_points(
        y_test, probability,
        approval_thresholds=(0.15, threshold, 0.25, 0.30),
        reject_threshold=DEFAULT_REVIEW_MAX,
    )

    age_frame = pd.DataFrame({"age": x_test["age"].to_numpy(), "default": np.asarray(y_test), "p": probability})
    age_frame["age_band"] = pd.cut(
        age_frame["age"], bins=[17, 29, 44, 59, 120],
        labels=["18–29", "30–44", "45–59", "60+"],
    )
    age_rows = []
    for band, group in age_frame.groupby("age_band", observed=True):
        approve = group["p"] < threshold
        review = (group["p"] >= threshold) & (group["p"] < 0.80)
        reject = group["p"] >= 0.80
        age_rows.append(
            f"| {band} | {len(group)} | {group['default'].mean():.3f} | "
            f"{approve.mean():.3f} | {review.mean():.3f} | {reject.mean():.3f} |"
        )

    metric_rows = [
        ("ROC-AUC", metrics.get("roc_auc"), "Ranking discrimination"),
        ("PR-AUC / Average Precision", metrics.get("pr_auc"), "Positive-class precision/recall ranking"),
        ("KS", metrics.get("ks"), "Maximum separation of default/non-default score distributions"),
        ("Brier score", metrics.get("brier_score"), "Lower is better for probability accuracy"),
        ("Expected calibration error (10 bins)", metrics.get("expected_calibration_error"), "Lower is better; binning-sensitive diagnostic"),
        ("Precision at tuned threshold", metrics.get("precision"), "Default precision using the saved threshold"),
        ("Recall at tuned threshold", metrics.get("recall"), "Default recall using the saved threshold"),
        ("F1 at tuned threshold", metrics.get("f1"), "Precision/recall harmonic mean"),
    ]
    metric_table = "\n".join(
        f"| {name} | {value:.4f} | {note} |" if value is not None else f"| {name} | n/a | {note} |"
        for name, value, note in metric_rows
    )
    threshold_table = "\n".join(
        f"| {p['approval_threshold']:.2f} | {p['approve']['rate']:.3f} | "
        f"{p['review']['rate']:.3f} | {p['reject']['rate']:.3f} | "
        f"{p['approve']['observed_default_rate']:.3f} | "
        f"{p['default_capture_rate_in_reject']:.3f}" if p['default_capture_rate_in_reject'] is not None else
        f"| {p['approval_threshold']:.2f} | {p['approve']['rate']:.3f} | {p['review']['rate']:.3f} | {p['reject']['rate']:.3f} | "
        f"{p['approve']['observed_default_rate']:.3f} | n/a"
        for p in points
    )
    calibration_table = "\n".join(
        f"| {b['lower']:.1f}–{b['upper']:.1f} | {b['count']} | "
        f"{b['mean_predicted_probability']:.3f} | {b['observed_default_rate']:.3f} |"
        for b in calibration_bins(y_test, probability)
    )

    return f"""# CreditFlow Model Card — {meta['version']}

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
| Model/version | `{meta['model_name']}` / `{meta['version']}` |
| Training data | `data/creditflow_dataset.csv`, deterministic synthetic proxy |
| Dataset SHA-256 | `{meta['dataset_sha256']}` |
| Rows / split | {meta['rows']} / stratified train-validation-test, seed {meta['seed']} |
| Test rows | {meta['split']['n_test']} |
| Positive label | Synthetic default indicator |
| Features | `{', '.join(meta['feature_order'])}` |
| Probability threshold | {threshold:.2f} approve cutoff; 0.80 reject cutoff; intermediate cases go to review |
| Selection objective | Validation business cost with FN cost {meta['fn_cost']:g} and FP cost {meta['fp_cost']:g} |

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
{metric_table}
| Accuracy | {metrics['accuracy']:.4f} | Context only; not the selection objective |

### Calibration bins

Equal-width bins; empty bins are omitted. ECE and Brier score are diagnostics,
not proof of calibration.

| Predicted probability | Cases | Mean predicted | Observed default rate |
|---|---:|---:|---:|
{calibration_table}

### Decision mix by candidate approve threshold

Reject cutoff remains 0.80, matching the current decision policy. Rates are
computed on the synthetic held-out test set; they are not approval targets.

| Approve cutoff | APPROVE rate | REVIEW rate | REJECT rate | Default rate among approved | Default capture in REJECT |
|---:|---:|---:|---:|---:|---:|
{threshold_table}

### Age-band diagnostic

| Age band | Cases | Observed default prevalence | APPROVE rate | REVIEW rate | REJECT rate |
|---|---:|---:|---:|---:|---:|
{chr(10).join(age_rows)}

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
"""


if __name__ == "__main__":
    output = ROOT / "docs/model-card.md"
    output.write_text(build_model_card(), encoding="utf-8")
    print(f"Wrote {output.relative_to(ROOT)}")
