"""Unit-scale contract tests: API nói VND, model ăn training-scale.

Quy ước khóa cứng:
- to_model_units(features) chia income/loan_amount/existing_debt cho
  VND_PER_MODEL_UNIT, giữ nguyên 5 trường còn lại.
- predict(VND payload) ≡ predict(to_model_units(VND payload)).
- 3 preset VND của UI có proba tăng dần theo rủi ro (monotonicity),
  và không đồng loạt một decision (spread tồn tại).
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.predict_service import (
    VND_PER_MODEL_UNIT,
    load_production_model,
    predict_risk,
    to_model_units,
)

IDEAL = {"income": 8000000, "age": 35, "employment_years": 8, "loan_amount": 120000000, "loan_term": 36, "existing_debt": 15000000, "credit_history": 9, "previous_defaults": 0}
TYPICAL = {"income": 2500000, "age": 32, "employment_years": 4, "loan_amount": 120000000, "loan_term": 36, "existing_debt": 35000000, "credit_history": 5, "previous_defaults": 0}
RISKY = {"income": 1500000, "age": 48, "employment_years": 1, "loan_amount": 600000000, "loan_term": 60, "existing_debt": 180000000, "credit_history": 1, "previous_defaults": 4}


def test_scale_constant_maps_vnd_into_training_range():
    # Training CSV: income 1500–19138, loan 3000–218312, debt 33–385301.
    # 1.5–20M VND / 1000 -> 1500–20000 units: khớp dải training.
    assert VND_PER_MODEL_UNIT == 1000.0
    out = to_model_units(IDEAL)
    assert out["income"] == 8000.0
    assert out["loan_amount"] == 120000.0
    assert out["existing_debt"] == 15000.0
    for k in ("age", "employment_years", "loan_term", "credit_history", "previous_defaults"):
        assert out[k] == IDEAL[k], f"{k} phải giữ nguyên"


def test_vnd_payload_equivalent_to_training_scale_payload():
    pipe, meta = load_production_model()
    training_scale = to_model_units(RISKY)
    a = predict_risk(pipe, dict(RISKY), meta)
    b = predict_risk(pipe, dict(training_scale), meta)
    assert a["risk_probability"] == b["risk_probability"]
    assert a["decision"] == b["decision"]


def test_preset_monotonicity_and_spread():
    pipe, meta = load_production_model()
    probs = [predict_risk(pipe, dict(p), meta)["risk_probability"] for p in (IDEAL, TYPICAL, RISKY)]
    assert probs[0] < probs[1] < probs[2], f"expected increasing risk, got {probs}"
    decisions = {predict_risk(pipe, dict(p), meta)["decision"] for p in (IDEAL, TYPICAL, RISKY)}
    assert len(decisions) >= 2, f"no decision spread: {decisions}"
