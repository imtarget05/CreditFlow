"""Pure Pandas feature engineering module for CreditFlow P3.

Derived features per docs/architecture/domains/credit-data-dictionary.md v0.1.
All formulas guarded against division by zero and invalid inputs.
No inf values are ever produced; edge cases yield NaN + flag strings.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DERIVED_FEATURES = [
    "debt_to_income",
    "loan_to_income",
    "debt_to_loan",
    "employment_stability",
    "credit_history_year_ratio",
]


def add_derived_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add 5 derived features to the dataframe with edge-case guards.

    Parameters
    ----------
    df : pd.DataFrame
        Raw input with columns: income, age, employment_years, loan_amount,
        loan_term, existing_debt, credit_history, previous_defaults.

    Returns
    -------
    tuple[pd.DataFrame, list[str]]
        (df_with_features, flags) where flags is a list of strings describing
        any edge cases found across all rows.

    Notes
    -----
    - Pure pandas/numpy only; no sklearn, no .fit(), no training code.
    - Never produces inf; invalid divisions yield NaN + flag.
    - Input dataframe is not mutated.
    """
    out = df.copy()
    flags: list[str] = []

    # --- debt_to_income = existing_debt / income ---
    # Guard: if income <= 0, set NaN + flag "income_zero_or_negative"
    income_valid = out["income"].astype("float64") > 0
    out["debt_to_income"] = pd.Series(np.nan, index=out.index, dtype="float64")
    out.loc[income_valid, "debt_to_income"] = (
        out.loc[income_valid, "existing_debt"].astype("float64")
        / out.loc[income_valid, "income"].astype("float64")
    )
    if not income_valid.all():
        flags.append("income_zero_or_negative")

    # --- loan_to_income = loan_amount / income ---
    # Guard: if income <= 0, set NaN + flag "income_zero_or_negative"
    out["loan_to_income"] = pd.Series(np.nan, index=out.index, dtype="float64")
    out.loc[income_valid, "loan_to_income"] = (
        out.loc[income_valid, "loan_amount"].astype("float64")
        / out.loc[income_valid, "income"].astype("float64")
    )
    if not income_valid.all() and "income_zero_or_negative" not in flags:
        flags.append("income_zero_or_negative")

    # --- debt_to_loan = existing_debt / loan_amount ---
    # Guard: if loan_amount <= 0, set NaN + flag "loan_amount_zero_or_negative"
    loan_valid = out["loan_amount"].astype("float64") > 0
    out["debt_to_loan"] = pd.Series(np.nan, index=out.index, dtype="float64")
    out.loc[loan_valid, "debt_to_loan"] = (
        out.loc[loan_valid, "existing_debt"].astype("float64")
        / out.loc[loan_valid, "loan_amount"].astype("float64")
    )
    if not loan_valid.all():
        flags.append("loan_amount_zero_or_negative")

    # --- employment_stability = min(employment_years / 10, 1.0) ---
    # Flag if employment_years > age - 18
    out["employment_stability"] = (
        out["employment_years"].astype("float64") / 10.0
    ).clip(upper=1.0)
    emp_outlier_mask = out["employment_years"].astype("float64") > (
        out["age"].astype("float64") - 18
    )
    if emp_outlier_mask.any():
        flags.append("employment_years_exceeds_age_minus_18")

    # --- credit_history_year_ratio = credit_history / max(age - 18, 1) ---
    # Flag if credit_history > age - 18; if age - 18 <= 0 set ratio to 0
    age_minus_18 = out["age"].astype("float64") - 18.0
    denom = np.where(age_minus_18 <= 0, 1.0, age_minus_18)
    ratio = out["credit_history"].astype("float64") / denom
    ratio = np.where(age_minus_18 <= 0, 0.0, ratio)
    out["credit_history_year_ratio"] = pd.Series(ratio, index=out.index, dtype="float64")
    ch_outlier_mask = out["credit_history"].astype("float64") > age_minus_18
    if ch_outlier_mask.any():
        flags.append("credit_history_exceeds_age_minus_18")

    # Ensure no inf values anywhere
    for col in DERIVED_FEATURES:
        out[col] = out[col].replace([np.inf, -np.inf], np.nan)

    return out, flags
