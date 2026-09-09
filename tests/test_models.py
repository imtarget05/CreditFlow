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
from pipeline.modeling.train import stratified_train_val_test_split


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
