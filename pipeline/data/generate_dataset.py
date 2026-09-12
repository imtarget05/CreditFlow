"""Deterministic synthetic credit dataset generator.

Creates a realistic-in-shape proxy tabular credit dataset (spec §19 constraint 2):
income, age, employment, loan, debt, credit history, previous defaults -> binary default.

The target is generated from a latent logistic risk function so that trained models
have genuinely learnable signal and class imbalance (~12% default), making the
4-model benchmark meaningful.

PROXY DISCLAIMER (spec §19.2): this is synthetic data used only to demonstrate the
ML pipeline. It is NOT a claim of having served real bank customers.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.validation.schemas import CANONICAL_COLUMNS, TARGET_COLUMN


def make_credit_dataset(n: int = 5000, random_state: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(random_state)

    # Correlated income, log-normal-ish, in "VND-like" monthly units.
    income = np.exp(rng.normal(7.7, 0.55, size=n)).astype(np.float64)  # ~2200..~42000
    income = np.clip(income, 1500.0, 60000.0)

    age = rng.randint(18, 81, size=n).astype(np.int64)
    max_work = np.maximum(age - 18, 0)
    employment_years = np.minimum(rng.uniform(0.0, max_work), max_work).astype(np.float64)

    loan_amount = income * rng.uniform(1.0, 18.0, size=n).astype(np.float64)
    loan_amount = np.clip(loan_amount, 3000.0, 1_000_000.0)

    loan_term = rng.randint(6, 61, size=n).astype(np.int64)

    existing_debt = np.clip(income * rng.uniform(0.0, 25.0, size=n), 0.0, None).astype(np.float64)

    max_ch = np.maximum(age - 18, 0)
    credit_history = np.minimum(rng.uniform(0.0, np.maximum(max_ch, 1)), max_ch).astype(
        np.float64
    )

    previous_defaults = rng.poisson(0.35, size=n).astype(np.int64)
    previous_defaults = np.where(rng.rand(n) < 0.03, rng.randint(3, 7, size=n), previous_defaults)

    # Latent risk (higher = more likely default).
    dti = existing_debt / np.maximum(income, 1.0)
    lti = loan_amount / np.maximum(income, 1.0)
    logit = (
        -3.2
        + 0.9 * _clip_log(dti + 1.0)                      # high debt burden -> risk
        + 0.6 * _clip_log(lti + 1.0)                      # large loan vs income -> risk
        + 0.7 * previous_defaults                         # history of defaults -> risk
        - 0.9 * _clip_log(employment_years + 1.0)         # stable employment -> safer
        - 0.5 * _clip_log(credit_history + 1.0)           # longer history -> safer
        - 0.015 * (age - 40)                              # age tilt (mild)
        - 0.00004 * (income - 2500.0)                     # higher income -> safer
    )
    prob = 1.0 / (1.0 + np.exp(-np.clip(logit, -12.0, 12.0)))
    target = (rng.rand(n) < np.clip(prob, 0.01, 0.97)).astype(np.int64)

    df = pd.DataFrame(
        {
            "income": income,
            "age": age,
            "employment_years": employment_years,
            "loan_amount": loan_amount,
            "loan_term": loan_term,
            "existing_debt": existing_debt,
            "credit_history": credit_history,
            "previous_defaults": previous_defaults,
            TARGET_COLUMN: target,
        }
    )
    return df


def _clip_log(x: np.ndarray) -> np.ndarray:
    return np.log(np.clip(x, 1e-2, None))


def write_dataset(path: str | Path, n: int = 5000, random_state: int = 42) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = make_credit_dataset(n=n, random_state=random_state)
    df.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/creditflow_dataset.csv")
    df = write_dataset(out)
    print(f"Wrote {df.shape[0]} rows x {df.shape[1]} cols -> {out}")
    print(f"Default rate: {df[TARGET_COLUMN].mean():.3%}")
    print(df.head())