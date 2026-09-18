"""Money-unit contract tests: API nói VND, model ăn training-scale.

Quy ước khóa cứng (single source of truth = pipeline/validation/schemas.py +
meta.json do scripts/train_models.py ghi ra):
- Divider (``vnd_per_model_unit``) đọc từ artifact, KHÔNG hardcode ở serving;
  ``to_model_units(features, meta)`` chia 3 trường tiền, giữ nguyên 5 trường còn lại.
- Payload không thể là VND (dưới floor hợp đồng, ví dụ income=2500) bị TỪ CHỐI
  rõ ràng — không âm thầm nhân/chia theo độ lớn.
- API(VND payload) ≡ persisted pipeline(training-scale features), conversion once.
- 3 preset VND của UI có proba tăng dần theo rủi ro (monotonicity).
"""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.predict_service import (
    ArtifactContractError,
    UnitContractViolation,
    load_production_model,
    money_scale,
    predict_risk,
    to_model_units,
    validate_model_bundle,
)
from pipeline.validation.schemas import (
    MIN_MONEY_VND,
    VND_PER_TRAINING_UNIT,
    validate_money_unit_contract,
)

IDEAL = {"income": 8000000, "age": 35, "employment_years": 8, "loan_amount": 120000000, "loan_term": 36, "existing_debt": 15000000, "credit_history": 9, "previous_defaults": 0}
TYPICAL = {"income": 2500000, "age": 32, "employment_years": 4, "loan_amount": 120000000, "loan_term": 36, "existing_debt": 35000000, "credit_history": 5, "previous_defaults": 0}
RISKY = {"income": 1500000, "age": 48, "employment_years": 1, "loan_amount": 600000000, "loan_term": 60, "existing_debt": 180000000, "credit_history": 1, "previous_defaults": 4}
# Same profile as the spec §4.1 example but expressed in the contract unit (VND).
SPEC_VND = {"income": 2500000, "age": 32, "employment_years": 4, "loan_amount": 12000000, "loan_term": 36, "existing_debt": 3500000, "credit_history": 5, "previous_defaults": 0}
# ... and the old spec numbers, which are training-scale and MUST be rejected.
SPEC_TRAINING_SCALE = {"income": 2500, "age": 32, "employment_years": 4, "loan_amount": 12000, "loan_term": 36, "existing_debt": 3500, "credit_history": 5, "previous_defaults": 0}


def test_service_converts_fixed_vnd_input_exactly_once():
    from unittest.mock import patch

    pipe, meta = load_production_model()
    original = dict(IDEAL)
    with patch.object(pipe, "predict_proba", wraps=pipe.predict_proba) as score:
        result = predict_risk(pipe, IDEAL, meta)

    score.assert_called_once()
    received = score.call_args.args[0]
    assert received.shape == (1, 13)
    assert list(received.columns) == meta["feature_order"]
    assert received.iloc[0]["income"] == 8000.0
    assert received.iloc[0]["loan_amount"] == 120000.0
    assert received.iloc[0]["existing_debt"] == 15000.0
    for field in ("age", "employment_years", "loan_term", "credit_history", "previous_defaults"):
        assert received.iloc[0][field] == original[field]
    assert IDEAL == original
    assert result["risk_probability"] == 0.0186
    assert result["decision"] == "APPROVE"


def test_artifact_declares_the_money_scale():
    """The divisor must come from the artifact, not from a hardcoded constant."""
    _pipe, meta = load_production_model()
    assert money_scale(meta) == VND_PER_TRAINING_UNIT == 1000.0
    assert meta["money_unit"] == "VND"
    assert meta["training_money_unit"] == "nghìn VND"


def test_scale_maps_vnd_into_training_range():
    # Training CSV: income 1500–19138, loan 3000–218312, debt 33–385301.
    # 1.5–20M VND / 1000 -> 1500–20000 units: khớp dải training.
    _pipe, meta = load_production_model()
    out = to_model_units(IDEAL, meta)
    assert out["income"] == 8000.0
    assert out["loan_amount"] == 120000.0
    assert out["existing_debt"] == 15000.0
    for k in ("age", "employment_years", "loan_term", "credit_history", "previous_defaults"):
        assert out[k] == IDEAL[k], f"{k} phải giữ nguyên"


def test_training_scale_payload_is_rejected_not_rescaled():
    """A magnitude heuristic would silently score this; the contract rejects it."""
    _pipe, meta = load_production_model()
    with pytest.raises(UnitContractViolation) as exc:
        to_model_units(SPEC_TRAINING_SCALE, meta)
    msg = str(exc.value)
    assert "VND" in msg and "income" in msg

    _pipe, meta = load_production_model()
    with pytest.raises(ValueError):
        predict_risk(_pipe, dict(SPEC_TRAINING_SCALE), meta)


def test_unit_contract_validator_flags_only_below_floor_money():
    assert validate_money_unit_contract(SPEC_VND) == []
    violations = validate_money_unit_contract(SPEC_TRAINING_SCALE)
    assert any("income" in v for v in violations)
    assert any("loan_amount" in v for v in violations)
    # existing_debt may legitimately be 0 -> no floor of its own
    assert not any("existing_debt" in v for v in violations)
    assert MIN_MONEY_VND["income"] == 1_000_000.0


@pytest.mark.parametrize("profile", [SPEC_VND, IDEAL, TYPICAL, RISKY])
def test_vnd_api_equivalent_to_direct_training_pipeline(profile):
    import pandas as pd
    from fastapi.testclient import TestClient
    from backend.app import app
    from pipeline.feature_engineering.features import add_derived_features
    from pipeline.modeling.threshold import business_decision

    pipe, meta = load_production_model()
    # Independent conversion oracle: never call the serving conversion helper.
    raw = dict(profile)
    for field in ("income", "loan_amount", "existing_debt"):
        raw[field] /= 1000.0
    fe, _ = add_derived_features(pd.DataFrame([raw]))
    probability = float(pipe.predict_proba(fe[meta["feature_order"]])[0, 1])
    with TestClient(app) as client:
        response = client.post("/predict", json=profile)
    assert response.status_code == 200
    result = response.json()
    assert result["risk_probability"] == round(probability, 4)
    assert result["decision"] == business_decision(probability, meta["threshold"])


def test_direct_pipeline_matches_api_service():
    """Same features through the persisted pipeline and through predict_risk."""
    import pandas as pd

    from pipeline.feature_engineering.features import add_derived_features

    pipe, meta = load_production_model()
    payload = predict_risk(pipe, dict(SPEC_VND), meta)

    fe, _ = add_derived_features(pd.DataFrame([to_model_units(SPEC_VND, meta)]))
    direct = float(pipe.predict_proba(fe[meta["feature_order"]])[0, 1])
    assert round(direct, 4) == payload["risk_probability"]


def test_prediction_is_independent_of_dict_key_order():
    pipe, meta = load_production_model()
    shuffled = list(SPEC_VND.items())
    shuffled.reverse()
    assert predict_risk(pipe, dict(shuffled), meta) == predict_risk(pipe, dict(SPEC_VND), meta)


def test_checked_in_model_bundle_has_verified_contract():
    """Corrupting any served artifact must be detected before it is loaded."""
    import hashlib
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    bundle = root / "models" / "production"
    manifest = json.loads((bundle / "manifest.json").read_text())

    assert validate_model_bundle() == manifest["metadata"]
    assert set(manifest["artifacts"]) == {
        "pipeline.joblib",
        "meta.json",
        "reference_stats.json",
        "benchmark_results.csv",
        "benchmark_results.json",
    }
    for filename, expected_sha256 in manifest["artifacts"].items():
        assert hashlib.sha256((bundle / filename).read_bytes()).hexdigest() == expected_sha256


def test_incomplete_bundle_metadata_is_rejected_before_model_loading(tmp_path):
    """Deleting a required serving field must fail contract validation, not score data."""
    import hashlib
    import json

    incomplete_meta = {
        "model_name": "test-model",
        "version": "test-v1",
        "sklearn_version": "1.9.0",
        "vnd_per_model_unit": 1000.0,
        # feature_order deliberately omitted
    }
    (tmp_path / "meta.json").write_text(json.dumps(incomplete_meta))
    manifest = {
        "bundle_version": 1,
        "metadata": incomplete_meta,
        "artifacts": {
            "meta.json": hashlib.sha256((tmp_path / "meta.json").read_bytes()).hexdigest(),
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ArtifactContractError, match="feature_order"):
        validate_model_bundle(tmp_path)


def test_preset_monotonicity_and_spread():
    pipe, meta = load_production_model()
    probs = [predict_risk(pipe, dict(p), meta)["risk_probability"] for p in (IDEAL, TYPICAL, RISKY)]
    assert probs[0] < probs[1] < probs[2], f"expected increasing risk, got {probs}"
    decisions = {predict_risk(pipe, dict(p), meta)["decision"] for p in (IDEAL, TYPICAL, RISKY)}
    assert len(decisions) >= 2, f"no decision spread: {decisions}"
