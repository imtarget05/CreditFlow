"""CreditFlow independent model validation pipeline (Plan 05, Gate 2).

Runs temporal backtest + calibration + subgroup analysis on the frozen eval
dataset against the approved production model. Evidence is written to
docs/evidence/e2e/model_validation_evidence.json.

This is NOT training: the model bundle is treated as immutable. The script
only scores and evaluates — it never writes to models/production/.

Usage:
  python scripts/model_validation.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.data.generate_dataset import TARGET_COLUMN
from pipeline.feature_engineering.features import add_derived_features
from pipeline.modeling.train import NUMERIC_COLUMNS
from pipeline.validation.schemas import validate_dataframe

DATASET_PATH = ROOT / "data" / "creditflow_dataset.csv"
MODEL_PATH = ROOT / "models" / "production" / "pipeline.joblib"
META_PATH = ROOT / "models" / "production" / "meta.json"
EVIDENCE_PATH = ROOT / "docs" / "evidence" / "e2e" / "model_validation_evidence.json"

FN_COST, FP_COST = 5.0, 1.0

GATES = {
    "max_normalized_business_cost": 0.42,
    "min_default_recall": 0.60,
    "min_f1": 0.45,
}


def _load_bundle():
    bundle = joblib.load(MODEL_PATH)
    meta = json.loads(META_PATH.read_text())
    return bundle, meta


def _business_cost(y_true, y_pred):
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    return fn * FN_COST + fp * FP_COST, tp, fp, tn, fn


def _ece(y_true, y_proba, n_bins: int = 10) -> float:
    """Expected Calibration Error (equal-width binning)."""
    y_true = np.asarray(y_true).astype(int)
    y_proba = np.asarray(y_proba, dtype=float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        if i < n_bins - 1:
            mask = (y_proba >= lo) & (y_proba < hi)
        else:
            mask = (y_proba >= lo) & (y_proba <= hi)
        if not mask.any():
            continue
        bin_acc = float(y_true[mask].mean())
        bin_conf = float(y_proba[mask].mean())
        ece += mask.mean() * abs(bin_conf - bin_acc)
    return round(ece, 4)


def _subgroup_analysis(df, y_true, y_proba, threshold):
    """Per-subgroup metrics: income, age, previous_defaults bands."""
    y_pred = (y_proba >= threshold).astype(int)
    subgroups = {}

    df = df.copy()
    df["income_band"] = pd.cut(df["income"], bins=3, labels=["low", "mid", "high"])
    for band in ["low", "mid", "high"]:
        mask = df["income_band"] == band
        if mask.any():
            cost, tp, _, _, fn = _business_cost(y_true[mask], y_pred[mask])
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            subgroups[f"income={band}"] = {
                "n": int(mask.sum()),
                "default_rate": round(float(y_true[mask].mean()), 4),
                "business_cost": cost,
                "normalized_cost": round(cost / int(mask.sum()), 4),
                "recall": round(recall, 4),
            }

    df["age_band"] = pd.cut(df["age"], bins=[17, 30, 50, 81], labels=["young", "mid", "older"])
    for band in ["young", "mid", "older"]:
        mask = df["age_band"] == band
        if mask.any():
            cost, tp, _, _, fn = _business_cost(y_true[mask], y_pred[mask])
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            subgroups[f"age={band}"] = {
                "n": int(mask.sum()),
                "default_rate": round(float(y_true[mask].mean()), 4),
                "business_cost": cost,
                "normalized_cost": round(cost / int(mask.sum()), 4),
                "recall": round(recall, 4),
            }

    df["prev_def_band"] = pd.cut(df["previous_defaults"], bins=[-1, 0, 1, 10], labels=["none", "few", "many"])
    for band in ["none", "few", "many"]:
        mask = df["prev_def_band"] == band
        if mask.any():
            cost, tp, _, _, fn = _business_cost(y_true[mask], y_pred[mask])
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            subgroups[f"previous_defaults={band}"] = {
                "n": int(mask.sum()),
                "default_rate": round(float(y_true[mask].mean()), 4),
                "business_cost": cost,
                "normalized_cost": round(cost / int(mask.sum()), 4),
                "recall": round(recall, 4),
            }

    return subgroups


def main():
    bundle, meta = _load_bundle()
    threshold = float(meta["threshold"])

    raw = pd.read_csv(DATASET_PATH)
    _, violations = validate_dataframe(raw)
    if violations:
        print(f"[warning] validation violations: {violations}")

    df, _ = add_derived_features(raw)
    df = df.dropna().reset_index(drop=True)

    # Temporal backtest: simulate time-ordered split (first 70% train/cal,
    # last 30% backtest). No target leakage: model already frozen.
    n = len(df)
    split_idx = int(n * 0.7)
    X_backtest = df.iloc[split_idx:]
    y_backtest = X_backtest[TARGET_COLUMN].values
    X_backtest_features = X_backtest[NUMERIC_COLUMNS]

    proba = bundle.predict_proba(X_backtest_features)[:, 1]
    y_pred = (proba >= threshold).astype(int)

    cost, tp, fp, tn, fn = _business_cost(y_backtest, y_pred)
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = float((y_pred == y_backtest).mean())

    ece = _ece(y_backtest, proba)
    subgroups = _subgroup_analysis(X_backtest, y_backtest, proba, threshold)

    normalized_cost = round(cost / len(X_backtest), 4)
    passed = (
        normalized_cost <= GATES["max_normalized_business_cost"]
        and recall >= GATES["min_default_recall"]
        and f1 >= GATES["min_f1"]
    )

    evidence = {
        "gate": "5",
        "title": "Independent Model Validation (temporal backtest + calibration + subgroup)",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": meta.get("version"),
        "model_name": meta.get("model_name"),
        "threshold": threshold,
        "dataset": str(DATASET_PATH.relative_to(ROOT)),
        "dataset_rows": n,
        "backtest_rows": len(X_backtest),
        "backtest_default_rate": float(y_backtest.mean()),
        "metrics": {
            "business_cost": cost,
            "normalized_business_cost": normalized_cost,
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "accuracy": round(accuracy, 4),
            "expected_calibration_error": ece,
        },
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "subgroups": subgroups,
        "gates": GATES,
        "passed": passed,
    }

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
