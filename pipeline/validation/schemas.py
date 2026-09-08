"""Canonical schema + validation for CreditFlow P2+P3.

Source of truth: docs/architecture/domains/credit-data-dictionary.md v0.1
Pure pandas validation — no sklearn, no .fit(), no training.
"""

from __future__ import annotations

import pandas as pd

CANONICAL_COLUMNS = [
    "income",
    "age",
    "employment_years",
    "loan_amount",
    "loan_term",
    "existing_debt",
    "credit_history",
    "previous_defaults",
]

TARGET_COLUMN = "default"

AGE_MIN, AGE_MAX = 18, 100

SCHEMA: dict[str, dict] = {
    "income": {
        "type": "float",
        "nullable": False,
        "range": (0.0, None),
        "rule": "> 0",
        "unit": "VND/tháng",
        "description": "Thu nhập tại thời điểm nộp hồ sơ",
    },
    "age": {
        "type": "int",
        "nullable": False,
        "range": (AGE_MIN, AGE_MAX),
        "rule": f"[{AGE_MIN}, {AGE_MAX}]",
        "unit": "năm",
        "description": "Tuổi tại thời điểm nộp hồ sơ",
    },
    "employment_years": {
        "type": "float",
        "nullable": False,
        "range": (0.0, None),
        "rule": ">= 0, <= age - 18",
        "unit": "năm",
        "description": "Số năm làm việc liên tục",
    },
    "loan_amount": {
        "type": "float",
        "nullable": False,
        "range": (0.0, None),
        "rule": "> 0",
        "unit": "VND",
        "description": "Số tiền vay đề nghị",
    },
    "loan_term": {
        "type": "int",
        "nullable": False,
        "range": (1, None),
        "rule": "> 0",
        "unit": "tháng",
        "description": "Kỳ hạn vay",
    },
    "existing_debt": {
        "type": "float",
        "nullable": False,
        "range": (0.0, None),
        "rule": ">= 0",
        "unit": "VND",
        "description": "Tổng dư nợ hiện hữu",
    },
    "credit_history": {
        "type": "float",
        "nullable": False,
        "range": (0.0, None),
        "rule": ">= 0, <= age - 18",
        "unit": "năm",
        "description": "Độ dài lịch sử tín dụng",
    },
    "previous_defaults": {
        "type": "int",
        "nullable": False,
        "range": (0, None),
        "rule": ">= 0",
        "unit": "count",
        "description": "Số lần default/vỡ nợ trước đây",
    },
}

DATA_DICTIONARY: dict[str, dict] = {
    "income": {
        "type": "float",
        "unit": "VND/tháng",
        "range": "> 0",
        "nullable": False,
        "description": "Thu nhập tại thời điểm nộp hồ sơ",
    },
    "age": {
        "type": "int",
        "unit": "năm",
        "range": "18–100",
        "nullable": False,
        "description": "Tuổi tại thời điểm nộp hồ sơ",
    },
    "employment_years": {
        "type": "float",
        "unit": "năm",
        "range": ">= 0, <= age - 18",
        "nullable": False,
        "description": "Số năm làm việc liên tục",
    },
    "loan_amount": {
        "type": "float",
        "unit": "VND",
        "range": "> 0",
        "nullable": False,
        "description": "Số tiền vay đề nghị",
    },
    "loan_term": {
        "type": "int",
        "unit": "tháng",
        "range": "> 0",
        "nullable": False,
        "description": "Kỳ hạn vay",
    },
    "existing_debt": {
        "type": "float",
        "unit": "VND",
        "range": ">= 0",
        "nullable": False,
        "description": "Tổng dư nợ hiện hữu",
    },
    "credit_history": {
        "type": "float",
        "unit": "năm",
        "range": ">= 0, <= age - 18",
        "nullable": False,
        "description": "Độ dài lịch sử tín dụng",
    },
    "previous_defaults": {
        "type": "int",
        "unit": "count",
        "range": ">= 0",
        "nullable": False,
        "description": "Số lần default/vỡ nợ trước đây",
    },
}


def _check_required_columns(df: pd.DataFrame) -> list[str]:
    """Kiểm tra tất cả 8 cột bắt buộc có tồn tại trong DataFrame."""
    violations: list[str] = []
    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    if missing:
        violations.append(f"missing_columns: {missing}")
    return violations


def _check_missing_values(df: pd.DataFrame) -> list[str]:
    """Kiểm tra giá trị thiếu (NaN) trong các cột bắt buộc."""
    violations: list[str] = []
    for col in CANONICAL_COLUMNS:
        if col in df.columns:
            n_missing = int(df[col].isna().sum())
            if n_missing > 0:
                violations.append(f"{col}: contains NaN ({n_missing} rows)")
    return violations


def _check_duplicate_rows(df: pd.DataFrame) -> list[str]:
    """Kiểm tra các hàng trùng lặp trong DataFrame."""
    violations: list[str] = []
    n_duplicates = int(df.duplicated().sum())
    if n_duplicates > 0:
        violations.append(f"duplicate_rows: {n_duplicates} duplicate rows found")
    return violations


def _check_range_rules(df: pd.DataFrame) -> list[str]:
    """Kiểm tra các quy tắc range cho từng cột theo SCHEMA."""
    violations: list[str] = []

    if "income" in df.columns:
        if (df["income"] <= 0).any():
            violations.append("income: must be > 0")

    if "loan_amount" in df.columns:
        if (df["loan_amount"] <= 0).any():
            violations.append("loan_amount: must be > 0")

    if "age" in df.columns:
        if ((df["age"] < AGE_MIN) | (df["age"] > AGE_MAX)).any():
            violations.append(f"age: must be in [{AGE_MIN},{AGE_MAX}]")

    if "loan_term" in df.columns:
        if (df["loan_term"] <= 0).any():
            violations.append("loan_term: must be > 0")

    for col in ("employment_years", "existing_debt", "credit_history", "previous_defaults"):
        if col in df.columns and (df[col] < 0).any():
            violations.append(f"{col}: must be >= 0")

    if "employment_years" in df.columns and "age" in df.columns:
        if (df["employment_years"] > (df["age"] - 18)).any():
            violations.append("employment_years: must be <= age - 18")

    if "credit_history" in df.columns and "age" in df.columns:
        if (df["credit_history"] > (df["age"] - 18)).any():
            violations.append("credit_history: must be <= age - 18")

    return violations


def validate_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Validate a DataFrame against the CreditFlow canonical schema.

    Checks all 8 required columns, range rules, missing values, and duplicate
    rows. Pure pandas — no sklearn, no .fit(), no training.

    Args:
        df: Input DataFrame with raw credit features.

    Returns:
        A tuple of (df, violations) where df is the input DataFrame
        (unmodified) and violations is a list of violation messages.
        An empty violations list means the DataFrame passes all checks.

    Example:
        >>> import pandas as pd
        >>> df = pd.DataFrame({"income": [2500], "age": [32], ...})
        >>> validated_df, violations = validate_dataframe(df)
        >>> if not violations:
        ...     print("Valid")
    """
    violations: list[str] = []

    violations.extend(_check_required_columns(df))
    violations.extend(_check_missing_values(df))
    violations.extend(_check_duplicate_rows(df))
    violations.extend(_check_range_rules(df))

    return df, violations
