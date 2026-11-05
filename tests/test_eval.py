"""Tests for the explanation eval harness (TDD RED first)."""
from __future__ import annotations

import pytest


def test_eval_schema_offline(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "")
    from scripts.eval_explanations import run_eval
    out = run_eval([{
        "income": 5000, "age": 35, "employment_years": 10,
        "loan_amount": 20000, "loan_term": 36,
        "existing_debt": 3000, "credit_history": 8, "previous_defaults": 0
    }])
    assert out["n"] == 1 and out["json_valid_rate"] == 1.0
    assert "reason_keyword_alignment_rate" in out


def test_probability_metrics_include_ranking_and_calibration():
    from pipeline.modeling.evaluate import calibration_bins, compute_metrics

    metrics = compute_metrics([0, 0, 1, 1], [0, 0, 1, 1], [0.1, 0.4, 0.6, 0.9])
    assert metrics["roc_auc"] == 1.0
    assert metrics["pr_auc"] == 1.0
    assert metrics["ks"] == 1.0
    assert metrics["brier_score"] == pytest.approx(0.085)
    assert metrics["expected_calibration_error"] == pytest.approx(0.25)
    assert [row["count"] for row in calibration_bins(
        [0, 0, 1, 1], [0.1, 0.4, 0.6, 0.9], 2
    )] == [2, 2]


def test_threshold_report_uses_three_way_credit_decisions():
    from pipeline.modeling.evaluate import threshold_operating_points

    point = threshold_operating_points(
        [0, 0, 1, 1], [0.1, 0.4, 0.6, 0.9], approval_thresholds=(0.3,), reject_threshold=0.8
    )[0]
    assert point["approve"]["rate"] == 0.25
    assert point["review"]["rate"] == 0.5
    assert point["reject"]["rate"] == 0.25
    assert point["default_capture_rate_in_reject"] == 0.5


def test_llm_judge_requires_credentials_before_network(monkeypatch):
    from scripts.eval_explanations import run_llm_judge

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID"):
        run_llm_judge([{"income": 1}], provider="cloudflare")
