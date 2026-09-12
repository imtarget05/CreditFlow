"""Tests for pipeline/monitoring/drift.py (P11 — ML drift monitoring)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.monitoring.drift import (
    DRIFT_DETECTED,
    INSUFFICIENT_DATA,
    NO_DRIFT,
    compute_prediction_reference,
    compute_reference_stats,
    detect_drift,
    psi,
)

FEATURES = ["income", "age", "loan_amount"]


def _reference(df: pd.DataFrame, proba: np.ndarray | None = None) -> dict:
    ref = compute_reference_stats(df, FEATURES)
    if proba is not None:
        ref["prediction"] = compute_prediction_reference(proba)
    return ref


def test_psi_identical_distribution_is_zero():
    rng = np.random.default_rng(42)
    sample = rng.normal(0, 1, 5000)
    edges = list(np.quantile(sample, np.linspace(0, 1, 11)))
    assert psi(edges, sample) < 0.02


def test_psi_shifted_distribution_is_high():
    rng = np.random.default_rng(42)
    ref_sample = rng.normal(0, 1, 5000)
    edges = list(np.quantile(ref_sample, np.linspace(0, 1, 11)))
    shifted = rng.normal(3, 1, 2000)  # large mean shift
    assert psi(edges, shifted) > 0.2


def test_psi_empty_actual_is_nan():
    assert np.isnan(psi([0, 1, 2], []))


def test_reference_stats_shape():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({f: rng.normal(size=100) for f in FEATURES})
    ref = _reference(df)
    assert ref["n_rows"] == 100
    assert set(ref["features"]) == set(FEATURES)
    for f in FEATURES:
        assert len(ref["features"][f]["bins"]) == 11


def test_detect_insufficient_data_below_min_samples():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({f: rng.normal(size=100) for f in FEATURES})
    ref = _reference(df)
    recent = pd.DataFrame({f: rng.normal(size=10) for f in FEATURES})
    report = detect_drift(ref, recent, min_samples=50)
    assert report["status"] == INSUFFICIENT_DATA


def test_detect_no_drift_on_same_distribution():
    rng = np.random.default_rng(7)
    base = pd.DataFrame({f: rng.normal(size=3000) for f in FEATURES})
    ref = _reference(base, rng.uniform(0, 1, 3000))
    recent = pd.DataFrame({f: rng.normal(size=200) for f in FEATURES})
    recent["risk_probability"] = rng.uniform(0, 1, 200)
    report = detect_drift(ref, recent, min_samples=50)
    assert report["status"] == NO_DRIFT
    assert report["prediction"]["status"] == NO_DRIFT


def test_detect_drift_on_shifted_feature():
    rng = np.random.default_rng(7)
    base = pd.DataFrame({f: rng.normal(size=3000) for f in FEATURES})
    ref = _reference(base)
    recent = pd.DataFrame(
        {
            "income": rng.normal(0, 1, 200),
            "age": rng.normal(0, 1, 200),
            "loan_amount": rng.normal(10, 1, 200),  # heavy shift
        }
    )
    report = detect_drift(ref, recent, min_samples=50)
    assert report["status"] == DRIFT_DETECTED
    assert report["features"]["loan_amount"]["status"] == DRIFT_DETECTED
    assert report["features"]["income"]["status"] in (NO_DRIFT, "WATCH")


def test_prediction_reference_used_when_present():
    rng = np.random.default_rng(3)
    base = pd.DataFrame({f: rng.normal(size=3000) for f in FEATURES})
    ref = _reference(base, rng.uniform(0, 1, 3000))
    recent = pd.DataFrame({f: rng.normal(size=150) for f in FEATURES})
    recent["risk_probability"] = rng.uniform(0.9, 1.0, 150)  # shifted predictions
    report = detect_drift(ref, recent, min_samples=50)
    assert report["prediction"]["status"] == DRIFT_DETECTED
    assert report["status"] == DRIFT_DETECTED


def test_detect_handles_missing_reference_feature():
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"income": rng.normal(size=200), "age": rng.normal(size=200)})
    ref = compute_reference_stats(df, ["income", "age"])
    recent = pd.DataFrame(
        {"income": rng.normal(size=100), "age": rng.normal(size=100), "loan_amount": 1.0}
    )
    report = detect_drift(ref, recent, min_samples=50)
    assert report["status"] in (NO_DRIFT, DRIFT_DETECTED)
    assert "loan_amount" not in report["features"]
