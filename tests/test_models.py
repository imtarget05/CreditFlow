"""Unit tests for pipeline/modeling module.

Uses plain assert statements so tests work with both:
  python -m pytest tests/test_models.py
  python tests/test_models.py
"""

from __future__ import annotations

import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from pipeline.feature_engineering.features import add_derived_features, DERIVED_FEATURES
from pipeline.modeling.models import get_model_factory
from pipeline.modeling.train import NUMERIC_COLUMNS, stratified_train_val_test_split


def _base_df(n: int = 200, random_state: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(random_state)
    income = rng.randint(1500, 15001, size=n).astype(float)
    age = rng.randint(18, 101, size=n)
    employment_years = rng.uniform(0, 1, size=n) * (age - 18)
    loan_amount = rng.randint(5000, 50001, size=n).astype(float)
    loan_term = rng.randint(12, 61, size=n)
    existing_debt = rng.randint(0, 5001, size=n).astype(float)
    credit_history = rng.uniform(0, 1, size=n) * (age - 18)
    previous_defaults = rng.poisson(0.3, size=n)
    default_frac = 0.12
    default = rng.binomial(1, default_frac, size=n)
    return pd.DataFrame({
        "income": income,
        "age": age,
        "employment_years": employment_years,
        "loan_amount": loan_amount,
        "loan_term": loan_term,
        "existing_debt": existing_debt,
        "credit_history": credit_history,
        "previous_defaults": previous_defaults,
        "default": default,
    })


def _prepared_df(n: int = 200, random_state: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(random_state)
    income = rng.randint(1500, 15001, size=n).astype(float)
    age = rng.randint(18, 101, size=n)
    employment_years = rng.uniform(0, 1, size=n) * (age - 18)
    loan_amount = rng.randint(5000, 50001, size=n).astype(float)
    loan_term = rng.randint(12, 61, size=n)
    existing_debt = rng.randint(0, 5001, size=n).astype(float)
    credit_history = rng.uniform(0, 1, size=n) * (age - 18)
    previous_defaults = rng.poisson(0.3, size=n)
    logit = (
        -3.0
        + 0.0002 * (income - 2500)
        - 0.02 * (age - 32)
        + 0.00005 * (loan_amount - 12000)
        + 0.0001 * (existing_debt - 3500)
        - 0.3 * np.minimum(employment_years / 10.0, 1.0)
        + 0.8 * previous_defaults
    )
    prob = 1.0 / (1.0 + np.exp(-logit))
    default = rng.binomial(1, np.clip(prob, 0.01, 0.99), size=n)
    df = pd.DataFrame({
        "income": income,
        "age": age,
        "employment_years": employment_years,
        "loan_amount": loan_amount,
        "loan_term": loan_term,
        "existing_debt": existing_debt,
        "credit_history": credit_history,
        "previous_defaults": previous_defaults,
        "default": default,
    })
    df, _ = add_derived_features(df)
    return df


def test_model_factory_returns_four_models():
    models = get_model_factory()
    assert len(models) == 4, f"Expected 4 models, got {len(models)}"
    names = [name for name, _ in models]
    assert len(names) == len(set(names)), "Model names must be unique"


def test_stratified_split_preserves_class_ratio():
    df = _base_df(n=300, random_state=42)
    X_train, X_val, X_test, y_train, y_val, y_test = stratified_train_val_test_split(df)
    p_full = df["default"].mean()
    p_train = y_train.mean()
    p_val = y_val.mean()
    p_test = y_test.mean()
    for p_split, label in [(p_train, "train"), (p_val, "val"), (p_test, "test")]:
        assert abs(p_split - p_full) < 0.05, f"{label} default rate drifted: {p_split} vs {p_full}"
    n_total = len(X_train) + len(X_val) + len(X_test)
    assert n_total == len(df), "Split should preserve all rows"


def test_stratified_split_shapes():
    df = _base_df(n=300, random_state=42)
    X_train, X_val, X_test, y_train, y_val, y_test = stratified_train_val_test_split(df)
    assert len(X_train) + len(X_val) + len(X_test) == len(df)
    assert len(X_train) == len(y_train)
    assert len(X_val) == len(y_val)
    assert len(X_test) == len(y_test)


def test_pipeline_fit_predict_shape():
    df = _prepared_df(n=200, random_state=42)
    X_train, X_val, X_test, y_train, y_val, y_test = stratified_train_val_test_split(df)
    name, model = get_model_factory()[0]
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from pipeline.modeling.train import NUMERIC_COLUMNS
    pipe = Pipeline([
        ("preprocessor", ColumnTransformer(transformers=[("num", StandardScaler(), NUMERIC_COLUMNS)])),
        ("model", model),
    ])
    pipe.fit(X_train, y_train)
    preds = pipe.predict(X_test)
    assert len(preds) == len(X_test), "Predictions length should match test set size"


def test_no_nan_in_predictions():
    df = _prepared_df(n=200, random_state=42)
    X_train, X_val, X_test, y_train, y_val, y_test = stratified_train_val_test_split(df)
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from pipeline.modeling.train import NUMERIC_COLUMNS
    for name, model in get_model_factory():
        pipe = Pipeline([
            ("preprocessor", ColumnTransformer(transformers=[("num", StandardScaler(), NUMERIC_COLUMNS)])),
            ("model", model),
        ])
        pipe.fit(X_train, y_train)
        proba = pipe.predict_proba(X_val)
        assert not np.isnan(proba).any(), f"{name} predict_proba contains NaN"
        assert not np.isinf(proba).any(), f"{name} predict_proba contains inf"


def _production_split():
    from pathlib import Path
    from backend.predict_service import load_production_model

    root = Path(__file__).resolve().parents[1]
    raw = pd.read_csv(root / "data" / "creditflow_dataset.csv")
    features, _ = add_derived_features(raw)
    return raw, load_production_model(), stratified_train_val_test_split(features)


def test_production_dataset_has_no_duplicate_or_overlapping_profiles():
    from itertools import combinations
    from pipeline.validation.schemas import CANONICAL_COLUMNS

    raw, (_, meta), split = _production_split()
    import hashlib
    from pathlib import Path
    dataset = Path(__file__).resolve().parents[1] / "data" / "creditflow_dataset.csv"
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == meta["dataset_sha256"]
    assert len(raw) == meta["rows"] == 5000
    assert not raw.duplicated(CANONICAL_COLUMNS).any()
    sets = [set(pd.util.hash_pandas_object(x[CANONICAL_COLUMNS], index=False)) for x in split[:3]]
    assert [len(x) for x in sets] == [3500, 750, 750]
    for a, b in combinations(sets, 2):
        assert not a.intersection(b)


def test_persisted_scaler_fitted_on_train_only():
    _, (pipe, meta), split = _production_split()
    train, val, test = split[:3]
    scaler = pipe.named_steps["preprocessor"].named_transformers_["num"]
    assert scaler.n_samples_seen_ == len(train) == 3500
    np.testing.assert_allclose(scaler.mean_, train[meta["feature_order"]].mean(), rtol=1e-12)
    np.testing.assert_allclose(scaler.var_, train[meta["feature_order"]].var(ddof=0), rtol=1e-12)
    before = scaler.mean_.copy()
    pipe.predict_proba(val)
    pipe.predict_proba(test)
    np.testing.assert_array_equal(before, scaler.mean_)
    assert list(pipe.feature_names_in_) == meta["feature_order"]
    assert "default" not in meta["feature_order"]


def test_production_threshold_and_test_metrics_reproduce():
    import json
    from pathlib import Path
    from pipeline.modeling.threshold import find_optimal_threshold
    from pipeline.modeling.evaluate import compute_metrics, calculate_confusion_matrix

    _, (pipe, meta), split = _production_split()
    _, val, test, _, y_val, y_test = split
    threshold = find_optimal_threshold(y_val, pipe.predict_proba(val)[:, 1])
    assert threshold["best_threshold"] == meta["threshold"] == 0.20
    assert threshold["best_cost"] == meta["business_cost"]
    assert type(pipe.named_steps["model"]).__name__ == "LogisticRegression"
    proba = pipe.predict_proba(test)[:, 1]
    pred = (proba >= meta["threshold"]).astype(int)
    metrics = compute_metrics(y_test, pred, proba)
    for name, value in meta["test_metrics"].items():
        assert round(float(metrics[name]), 4) == value
    tn, fp, fn, tp = calculate_confusion_matrix(y_test, pred).ravel()
    assert (tn, fp, fn, tp) == (558, 92, 30, 70)
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "models/production/benchmark_results.json").read_text())
    winner = sorted(rows, key=lambda row: (row["business_cost"], -row["val_f1"]))[0]
    assert winner["model"] == meta["model_name"]
    assert winner["best_threshold"] == meta["threshold"]
    reference = json.loads((root / "models/production/reference_stats.json").read_text())
    assert reference["n_rows"] == 3500


def test_model_bundle_metadata_matches_persisted_pipeline_contract():
    """The manifest's immutable metadata must be exactly what serving consumes."""
    from backend.predict_service import validate_model_bundle

    meta = validate_model_bundle()
    assert meta["feature_order"] == list(NUMERIC_COLUMNS)
    assert meta["vnd_per_model_unit"] == 1000.0



if __name__ == "__main__":
    test_functions = [
        test_model_factory_returns_four_models,
        test_stratified_split_preserves_class_ratio,
        test_stratified_split_shapes,
        test_pipeline_fit_predict_shape,
        test_no_nan_in_predictions,
    ]
    for fn in test_functions:
        fn()
        print(f"  PASS: {fn.__name__}")
    print(f"\nAll {len(test_functions)} tests passed.")
