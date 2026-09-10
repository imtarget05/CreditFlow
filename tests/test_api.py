"""Integration tests for the FastAPI service (backend.app).

These use FastAPI TestClient to exercise real request handling, Pydantic validation,
and the prediction path against the trained production model on disk.

NOTE: These are automated internal-correctness tests. User-facing acceptance is
verified separately via live HTTP/curl + browser (see docs/qa/manual-acceptance.md).
Requires a trained model at models/production/ (run scripts/train_models.py first).
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import ExitStack

from fastapi.testclient import TestClient

from backend.app import app, metrics

# Keep the app lifespan (model load) open for the whole test module.
_stack = ExitStack()
client = _stack.enter_context(TestClient(app))


VALID = {
    "income": 8000000.0,
    "age": 35,
    "employment_years": 8.0,
    "loan_amount": 120000000.0,
    "loan_term": 36,
    "existing_debt": 15000000.0,
    "credit_history": 9.0,
    "previous_defaults": 0,
}


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_version"]


def test_predict_valid_returns_full_payload():
    r = client.post("/predict", json=VALID)
    assert r.status_code == 200
    body = r.json()
    for field in ("risk_probability", "risk_level", "decision", "model_version", "model_name"):
        assert field in body, f"missing {field}"
    assert 0.0 <= body["risk_probability"] <= 1.0
    assert body["decision"] in {"APPROVE", "REVIEW", "REJECT"}
    # Decision now exposes the cost-tuned threshold used for the decision.
    assert "tuned_threshold" in body["threshold"], "threshold must include tuned_threshold"
    assert "approve_max" in body["threshold"]
    assert "review_max" in body["threshold"]


def test_predict_decision_consistent_with_tuned_threshold():
    """The decision must follow the cost-tuned threshold, not the display buckets."""
    import pipeline.modeling.threshold as t

    for p in (VALID, dict(VALID, previous_defaults=3), dict(VALID, previous_defaults=6)):
        r = client.post("/predict", json=p)
        assert r.status_code == 200
        body = r.json()
        tuned = float(body["threshold"]["tuned_threshold"])
        prob = body["risk_probability"]
        expected = (
            "APPROVE" if prob < tuned
            else "REVIEW" if prob < t.DEFAULT_REVIEW_MAX
            else "REJECT"
        )
        assert body["decision"] == expected, f"prob={prob}, tuned={tuned}, got {body['decision']}"


def test_predict_missing_field_422():
    payload = {k: v for k, v in VALID.items() if k != "income"}
    r = client.post("/predict", json=payload)
    assert r.status_code == 422


def test_predict_wrong_datatype_422():
    payload = dict(VALID, income="two-thousand")
    r = client.post("/predict", json=payload)
    assert r.status_code == 422


def test_predict_negative_income_422():
    payload = dict(VALID, income=-500)
    r = client.post("/predict", json=payload)
    assert r.status_code == 422


def test_predict_age_boundary_ok():
    payload = dict(VALID, age=100, employment_years=0, credit_history=0)
    r = client.post("/predict", json=payload)
    assert r.status_code == 200


def test_predict_employment_gt_age_minus_18_422():
    payload = dict(VALID, age=30, employment_years=20)
    r = client.post("/predict", json=payload)
    assert r.status_code == 422


def test_model_info_has_version_and_metrics():
    r = client.get("/model/info")
    assert r.status_code == 200
    model = r.json()["model"]
    assert "version" in model
    assert "val_metrics" in model


def test_metrics_runtime_counters_present():
    before = metrics.snapshot()
    client.post("/predict", json=VALID)
    after = metrics.snapshot()
    assert after["requests"]["predict"] >= before["requests"]["predict"] + 1 or after["requests"]["total"] > before["requests"]["total"]
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "runtime" in r.json()
    assert "benchmark" in r.json()


def test_drift_endpoint_report_shape():
    """GET /drift returns a well-formed drift report (P11)."""
    r = client.get("/drift")
    assert r.status_code == 200
    body = r.json()
    assert "drift" in body and "reference" in body
    drift = body["drift"]
    assert drift["status"] in ("NO_DRIFT", "DRIFT_DETECTED", "INSUFFICIENT_DATA")
    assert "note" in drift  # data-drift ≠ performance-degradation statement
    assert "features" in drift and "prediction" in drift
    assert "reference_stats.json" in body["reference"]["source"]


def test_drift_status_matches_window_size():
    """Empty/small window must report INSUFFICIENT_DATA (min_samples guard)."""
    from backend.app import prediction_window

    n_before = len(prediction_window)
    r = client.get("/drift")
    drift = r.json()["drift"]
    if n_before < drift["min_samples"]:
        assert drift["status"] == "INSUFFICIENT_DATA"
        assert drift["n_recent"] == n_before


if __name__ == "__main__":
    fns = [
        test_health_ok,
        test_predict_valid_returns_full_payload,
        test_predict_missing_field_422,
        test_predict_wrong_datatype_422,
        test_predict_negative_income_422,
        test_predict_age_boundary_ok,
        test_predict_employment_gt_age_minus_18_422,
        test_model_info_has_version_and_metrics,
        test_metrics_runtime_counters_present,
        test_drift_endpoint_report_shape,
        test_drift_status_matches_window_size,
    ]
    for fn in fns:
        fn()
        print(f"  PASS: {fn.__name__}")
    print(f"\nAll {len(fns)} API tests passed.")