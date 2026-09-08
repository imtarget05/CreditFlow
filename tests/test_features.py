"""Unit tests for feature engineering and validation modules.

Uses plain assert statements so tests work with both:
  python -m pytest tests/test_features.py
  python tests/test_features.py
"""

import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from pipeline.feature_engineering.features import add_derived_features, DERIVED_FEATURES
from pipeline.validation.schemas import validate_dataframe, CANONICAL_COLUMNS, AGE_MIN, AGE_MAX


def _base_row(**overrides):
    row = {
        "income": 2500.0,
        "age": 32,
        "employment_years": 4.0,
        "loan_amount": 12000.0,
        "loan_term": 36,
        "existing_debt": 3500.0,
        "credit_history": 5.0,
        "previous_defaults": 0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _assert_close(actual, expected, tol=1e-9, msg=""):
    assert abs(float(actual) - float(expected)) <= tol, f"{msg}: {actual} != {expected}"


# ---------------------------------------------------------------------------
# Tests for add_derived_features()
# ---------------------------------------------------------------------------

def test_normal_case_all_features_compute():
    out, flags = add_derived_features(_base_row())
    assert isinstance(out, pd.DataFrame), "Output should be a DataFrame"
    assert isinstance(flags, list), "Flags should be a list"
    _assert_close(out.loc[0, "debt_to_income"], 1.4, msg="debt_to_income")
    _assert_close(out.loc[0, "loan_to_income"], 4.8, msg="loan_to_income")
    _assert_close(out.loc[0, "debt_to_loan"], 3500.0 / 12000.0, msg="debt_to_loan")
    _assert_close(out.loc[0, "employment_stability"], 0.4, msg="employment_stability")
    _assert_close(out.loc[0, "credit_history_year_ratio"], 5.0 / 14.0, msg="credit_history_year_ratio")
    for c in DERIVED_FEATURES:
        assert c in out.columns, f"{c} missing from output"


def test_div_by_zero_income_zero():
    out, flags = add_derived_features(_base_row(income=0.0))
    assert math.isnan(out.loc[0, "debt_to_income"]), "debt_to_income must be NaN when income=0"
    assert math.isnan(out.loc[0, "loan_to_income"]), "loan_to_income must be NaN when income=0"
    assert "income_zero_or_negative" in flags, "Should flag income_zero_or_negative"
    v = out.loc[0, "debt_to_income"]
    assert not math.isinf(v), "debt_to_income must not be inf"
    v = out.loc[0, "loan_to_income"]
    assert not math.isinf(v), "loan_to_income must not be inf"


def test_div_by_zero_loan_amount_zero():
    out, flags = add_derived_features(_base_row(loan_amount=0.0))
    assert math.isnan(out.loc[0, "debt_to_loan"]), "debt_to_loan must be NaN when loan_amount=0"
    assert "loan_amount_zero_or_negative" in flags, "Should flag loan_amount_zero_or_negative"
    v = out.loc[0, "debt_to_loan"]
    assert not math.isinf(v), "debt_to_loan must not be inf"


def test_age_18_credit_history_year_ratio_denominator():
    out, flags = add_derived_features(_base_row(age=18, credit_history=5.0))
    # max(age - 18, 1) = max(0, 1) = 1, but ratio is set to 0.0 when age-18 <= 0
    assert out.loc[0, "credit_history_year_ratio"] == 0.0, "ratio should be 0.0 when age=18"


def test_employment_years_exceeds_age_minus_18():
    out, flags = add_derived_features(_base_row(age=30, employment_years=20.0))
    assert "employment_years_exceeds_age_minus_18" in flags, "Should flag employment_years_exceeds_age_minus_18"


def test_credit_history_exceeds_age_minus_18():
    out, flags = add_derived_features(_base_row(age=30, credit_history=20.0))
    assert "credit_history_exceeds_age_minus_18" in flags, "Should flag credit_history_exceeds_age_minus_18"


def test_no_inf_in_derived_columns():
    out, flags = add_derived_features(_base_row(income=0.0, loan_amount=0.0))
    for c in DERIVED_FEATURES:
        v = out.loc[0, c]
        if isinstance(v, (float, np.floating)):
            assert not math.isinf(v), f"{c} must not be inf"


def test_no_mutate_input():
    df = _base_row()
    cols_before = list(df.columns)
    add_derived_features(df)
    assert list(df.columns) == cols_before, "input DataFrame must not be mutated"


# ---------------------------------------------------------------------------
# Tests for validate_dataframe()
# ---------------------------------------------------------------------------

def test_valid_dataframe_passes():
    df = _base_row()
    validated_df, violations = validate_dataframe(df)
    assert violations == [], f"Valid df should have no violations: {violations}"
    assert validated_df is df, "Should return the same DataFrame"


def test_missing_columns_detected():
    df = pd.DataFrame([{"income": 2500.0, "age": 32}])
    _, violations = validate_dataframe(df)
    assert len(violations) > 0, "Missing columns should produce violations"
    assert any("missing_columns" in v for v in violations), "Should report missing_columns"


def test_income_zero_rejected():
    df = _base_row(income=0)
    _, violations = validate_dataframe(df)
    assert any("income: must be > 0" in v for v in violations), "Should report income error"


def test_income_negative_rejected():
    df = _base_row(income=-100.0)
    _, violations = validate_dataframe(df)
    assert any("income: must be > 0" in v for v in violations)


def test_age_out_of_range_low():
    df = _base_row(age=17)
    _, violations = validate_dataframe(df)
    assert any("age: must be in" in v for v in violations), "Should report age error"


def test_age_out_of_range_high():
    df = _base_row(age=101)
    _, violations = validate_dataframe(df)
    assert any("age: must be in" in v for v in violations)


def test_negative_employment_years_rejected():
    df = _base_row(employment_years=-1.0)
    _, violations = validate_dataframe(df)
    assert any("employment_years: must be >= 0" in v for v in violations)


def test_negative_existing_debt_rejected():
    df = _base_row(existing_debt=-100.0)
    _, violations = validate_dataframe(df)
    assert any("existing_debt: must be >= 0" in v for v in violations)


def test_negative_previous_defaults_rejected():
    df = _base_row(previous_defaults=-1)
    _, violations = validate_dataframe(df)
    assert any("previous_defaults: must be >= 0" in v for v in violations)


def test_duplicate_rows_detected():
    df = pd.DataFrame([
        {"income": 2500.0, "age": 32, "employment_years": 4.0, "loan_amount": 12000.0,
         "loan_term": 36, "existing_debt": 3500.0, "credit_history": 5.0, "previous_defaults": 0},
        {"income": 2500.0, "age": 32, "employment_years": 4.0, "loan_amount": 12000.0,
         "loan_term": 36, "existing_debt": 3500.0, "credit_history": 5.0, "previous_defaults": 0},
    ])
    _, violations = validate_dataframe(df)
    assert any("duplicate_rows" in v for v in violations), "Should report duplicate rows"


def test_validate_dataframe_nan_detected():
    df = _base_row(income=float("nan"))
    _, violations = validate_dataframe(df)
    assert any("income" in v and "NaN" in v for v in violations), "Should report NaN"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_functions = [
        test_normal_case_all_features_compute,
        test_div_by_zero_income_zero,
        test_div_by_zero_loan_amount_zero,
        test_age_18_credit_history_year_ratio_denominator,
        test_employment_years_exceeds_age_minus_18,
        test_credit_history_exceeds_age_minus_18,
        test_no_inf_in_derived_columns,
        test_no_mutate_input,
        test_valid_dataframe_passes,
        test_missing_columns_detected,
        test_income_zero_rejected,
        test_income_negative_rejected,
        test_age_out_of_range_low,
        test_age_out_of_range_high,
        test_negative_employment_years_rejected,
        test_negative_existing_debt_rejected,
        test_negative_previous_defaults_rejected,
        test_duplicate_rows_detected,
        test_validate_dataframe_nan_detected,
    ]
    for fn in test_functions:
        fn()
        print(f"  PASS: {fn.__name__}")
    print(f"\nAll {len(test_functions)} tests passed.")
