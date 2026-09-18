"""Offline benchmark reproduction; no MLflow, network or serving-artifact writes.

Run from the repository root: .venv/bin/python scripts/audit_benchmark.py
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pipeline.feature_engineering.features import add_derived_features
from pipeline.modeling.evaluate import compute_metrics, calculate_confusion_matrix
from pipeline.modeling.models import get_model_factory
from pipeline.modeling.threshold import find_optimal_threshold
from pipeline.modeling.train import NUMERIC_COLUMNS, stratified_train_val_test_split
from pipeline.validation.schemas import CANONICAL_COLUMNS


def audit() -> dict:
    dataset = ROOT / "data/creditflow_dataset.csv"
    raw = pd.read_csv(dataset)
    assert len(raw) == 5000, "Expected the synthetic 5,000-row portfolio dataset"
    assert not raw.duplicated(CANONICAL_COLUMNS).any(), "Duplicate profiles"
    frame, _ = add_derived_features(raw)
    assert not frame.isna().any().any()
    train, val, test, y_train, y_val, y_test = stratified_train_val_test_split(frame)
    hashes = [set(pd.util.hash_pandas_object(x[CANONICAL_COLUMNS], index=False))
              for x in (train, val, test)]
    assert all(not hashes[i] & hashes[j] for i, j in ((0, 1), (0, 2), (1, 2)))
    fitted = {}
    rows = []
    for name, model in get_model_factory():
        pipe = Pipeline([
            ("preprocessor", ColumnTransformer([("num", StandardScaler(), NUMERIC_COLUMNS)])),
            ("model", model),
        ])
        pipe.fit(train, y_train)
        scaler = pipe.named_steps["preprocessor"].named_transformers_["num"]
        assert scaler.n_samples_seen_ == len(train)
        np.testing.assert_allclose(scaler.mean_, train[NUMERIC_COLUMNS].mean(), rtol=1e-12)
        probability = pipe.predict_proba(val)[:, 1]
        tuned = find_optimal_threshold(y_val, probability)
        pred = (probability >= tuned["best_threshold"]).astype(int)
        rows.append({"model": name, "threshold": float(tuned["best_threshold"]),
                     "validation_cost": float(tuned["best_cost"]),
                     "validation": compute_metrics(y_val, pred, probability),
                     "validation_confusion_matrix": calculate_confusion_matrix(y_val, pred).tolist(),
                     "config": model.get_params()})
        fitted[name] = pipe
    # Lock the selection before any test-set evaluation.
    winner = min(rows, key=lambda r: (r["validation_cost"], -r["validation"]["f1"]))
    selected_model, selected_threshold = winner["model"], winner["threshold"]
    benchmark = json.loads((ROOT / "models/production/benchmark_results.json").read_text())
    expected = {r["model"]: r for r in benchmark}
    for row in rows:
        probability = fitted[row["model"]].predict_proba(test)[:, 1]
        pred = (probability >= row["threshold"]).astype(int)
        row["test"] = compute_metrics(y_test, pred, probability)
        cm = calculate_confusion_matrix(y_test, pred)
        row["test_confusion_matrix"] = cm.tolist()
        row["test_cost"] = int(5 * cm[1, 0] + cm[0, 1])
        baseline = expected[row["model"]]
        assert row["threshold"] == baseline["best_threshold"]
        assert row["validation_cost"] == baseline["business_cost"]
        for split_name, key in (("validation", "val"), ("test", "test")):
            for metric, value in row[split_name].items():
                assert round(float(value), 4) == baseline[f"{key}_{metric}"], (row["model"], key, metric)
    meta = json.loads((ROOT / "models/production/meta.json").read_text())
    assert (selected_model, selected_threshold) == (meta["model_name"], meta["threshold"])
    return {
        "dataset": "synthetic 5,000 rows", "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "seed": 42, "split_sizes": [len(train), len(val), len(test)],
        "environment": {"python": platform.python_version(), **{p: version(p) for p in
                        ("numpy", "pandas", "scikit-learn", "xgboost", "joblib")}},
        "cost_assumption": "5 * FN + 1 * FP; hypothetical units, not VND losses",
        "confusion_matrix_order": "rows actual [0,1], columns predicted [0,1]",
        "selection": "minimum validation cost, tie-break validation F1; test reporting only",
        "selected_model": selected_model, "selected_threshold": selected_threshold,
        "tolerance": "metrics match production benchmark after rounding to 4 decimals",
        "checks": {"duplicate_profiles": 0, "cross_split_overlaps": 0, "scaler_fit": "train only"},
        "models": rows,
    }


if __name__ == "__main__":
    report = audit()
    output = ROOT / "docs/qa/benchmark-audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str, allow_nan=True) + "\n")
    print(f"PASS: {output}")
