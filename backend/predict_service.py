"""CreditFlow prediction service — prediction logic + decision engineering.

Wraps the production model so the HTTP layer stays thin. Loads the persisted
Pipeline (preprocessor + model), runs validation + feature engineering, then
returns risk_probability / risk_level / decision / model_version + reasons.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd

from pipeline.validation.schemas import validate_dataframe
from pipeline.feature_engineering.features import add_derived_features
from pipeline.modeling.threshold import (
    business_decision,
    DEFAULT_APPROVE_MAX,
    DEFAULT_REVIEW_MAX,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL_FILE = ROOT / "models" / "production" / "pipeline.joblib"
META_FILE = ROOT / "models" / "production" / "meta.json"

VND_PER_MODEL_UNIT = 1000.0
"""Contract VND -> training-scale divisor (scale-alignment, NOT an FX rate).

API contract, UI và data dictionary đều nói VND. Artifact production lại train
trên synthetic data thang ~10^3 (income mean 2664). Chia 3 trường tiền cho 1000
đưa 1.5–20M VND về 1500–20000 units ≈ dải training (min–max), đồng thời giữ
nguyên mọi tỉ số (DTI/LTI/debt-to-loan) vì tử và mẫu cùng chia một số.
"""


def to_model_units(features: dict) -> dict:
    """Map contract-VND money fields into the model's training scale.

    Chỉ chạm income/loan_amount/existing_debt; 5 trường còn lại giữ nguyên.
    Không validate ở đây — validation đã chạy trước trên giá trị VND gốc.
    """
    out = dict(features)
    for key in ("income", "loan_amount", "existing_debt"):
        out[key] = float(features[key]) / VND_PER_MODEL_UNIT
    return out


class ModelUnavailableError(RuntimeError):
    """Raised when the production model cannot be loaded."""


def load_production_model():
    if not MODEL_FILE.exists():
        raise ModelUnavailableError(
            f"Production model not found at {MODEL_FILE}. Run `python scripts/train_models.py` first."
        )
    pipe = joblib.load(MODEL_FILE)
    meta = json.loads(META_FILE.read_text()) if META_FILE.exists() else {"version": "unknown"}
    return pipe, meta


def predict_risk(pipeline, features: dict, meta: dict) -> dict:
    """Validate, featurize, and predict from raw feature dict -> decision payload."""
    # -- validation (business rules -> reject clearly invalid input) --
    df = pd.DataFrame([features])
    _violations, violations = validate_dataframe(df)
    if violations:
        raise ValueError(f"invalid_credit_profile: {'; '.join(violations)}")

    model_df = pd.DataFrame([to_model_units(features)])
    fe, _flags = add_derived_features(model_df)
    cols = [c for c in fe.columns if c != "default"]
    proba = float(pipeline.predict_proba(fe[cols])[0, 1])

    # Decision engineering (Phase 4): the DECISION is driven by the cost-tuned
    # threshold trained into meta.json (e.g. 0.2), while the displayed
    # risk_level uses the fixed LOW/MEDIUM/HIGH buckets (0.5 / 0.8).
    tuned_threshold = float(meta.get("threshold", DEFAULT_APPROVE_MAX))
    risk_level = _risk_bucket(proba)
    decision = business_decision(proba, tuned_threshold, DEFAULT_REVIEW_MAX)
    reasons = _build_reasons(fe.iloc[0])

    return {
        "risk_probability": round(proba, 4),
        "risk_level": risk_level,
        "decision": decision,
        "model_version": meta.get("version", "unknown"),
        "model_name": meta.get("model_name", "unknown"),
        "threshold": {
            "tuned_threshold": tuned_threshold,
            "approve_max": DEFAULT_APPROVE_MAX,
            "review_max": DEFAULT_REVIEW_MAX,
        },
        "reasons": reasons,
        "deployment": "API",
    }


def _risk_bucket(probability: float) -> str:
    if probability < DEFAULT_APPROVE_MAX:
        return "LOW"
    if probability < DEFAULT_REVIEW_MAX:
        return "MEDIUM"
    return "HIGH"


def _build_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    dti = float(row.get("debt_to_income", 0) or 0)
    lti = float(row.get("loan_to_income", 0) or 0)
    hist = float(row.get("credit_history", 0) or 0)
    prev = float(row.get("previous_defaults", 0) or 0)
    if dti > 0.4:
        reasons.append("high debt-to-income ratio")
    if lti > 5.0:
        reasons.append("high loan-to-income ratio")
    if hist < 2.0:
        reasons.append("short credit history")
    if prev > 0:
        reasons.append("history of missed payments")
    return reasons or ["low overall risk profile"]