"""Unit tests for decision/threshold engineering (pipeline.modeling.threshold).

These are pure-logic tests for internal correctness (not user-facing acceptance).
Covers the decision buckets and the cost-sensitive threshold search.
"""
from __future__ import annotations

import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.modeling.threshold import (
    assign_risk_level,
    decision_for_probability,
    business_decision,
    total_business_cost,
    find_optimal_threshold,
    cost_matrix,
    DEFAULT_APPROVE_MAX,
    DEFAULT_REVIEW_MAX,
    DEFAULT_FN_COST,
    DEFAULT_FP_COST,
    RISK_APPROVE,
    RISK_REVIEW,
    RISK_REJECT,
)


def test_decision_buckets_below_approve():
    assert assign_risk_level(0.10) == RISK_APPROVE


def test_decision_bucket_review_mid():
    assert assign_risk_level(0.65) == RISK_REVIEW


def test_decision_bucket_reject_high():
    assert assign_risk_level(0.90) == RISK_REJECT


def test_decision_boundary_approve():
    # 0.49 (< 0.5) -> APPROVE; exactly 0.5 -> REVIEW
    assert assign_risk_level(0.49) == RISK_APPROVE
    assert assign_risk_level(DEFAULT_APPROVE_MAX) == RISK_REVIEW


def test_decision_for_probability_matches():
    assert decision_for_probability(0.05) == assign_risk_level(0.05)


def test_cost_matrix_fn_costlier():
    cm = cost_matrix()
    assert cm[(1, 0)] > cm[(0, 1)]
    assert cm[(1, 0)] == DEFAULT_FN_COST
    assert cm[(0, 1)] == DEFAULT_FP_COST


def test_total_business_cost_weights():
    y_true = [1, 1, 0, 0]
    y_pred = [0, 1, 1, 0]
    # FN=1 (cost 5), FP=1 (cost 1) -> 6
    assert total_business_cost(y_true, y_pred) == 6.0


def test_find_optimal_threshold_prefers_lower_cost_on_imbalanced_reject():
    y_true = [1] * 20 + [0] * 20
    # strongly correct probabilities
    y_proba_hi = [0.9] * 20 + [0.1] * 20
    res = find_optimal_threshold(y_true, y_proba_hi)
    assert 0.0 <= res["best_threshold"] <= 1.0
    assert res["best_cost"] <= 20 * DEFAULT_FP_COST  # at most all-FP if threshold too low


def test_find_optimal_threshold_returns_consistent_curve():
    y_true = [1, 1, 0, 1, 0, 1]
    y_proba = [0.9, 0.7, 0.2, 0.8, 0.1, 0.6]
    res = find_optimal_threshold(y_true, y_proba)
    assert len(res["thresholds"]) == len(res["costs"])
    assert isinstance(res["best_threshold"], float)
    assert math.isfinite(res["best_cost"])


def test_business_decision_uses_tuned_threshold():
    # tuned threshold 0.2: prob below 0.2 -> APPROVE; 0.2..0.8 -> REVIEW; >=0.8 -> REJECT
    assert business_decision(0.05, approve_threshold=0.2) == RISK_APPROVE
    assert business_decision(0.20, approve_threshold=0.2) == RISK_REVIEW   # at boundary
    assert business_decision(0.30, approve_threshold=0.2) == RISK_REVIEW
    assert business_decision(0.79, approve_threshold=0.2) == RISK_REVIEW
    assert business_decision(0.80, approve_threshold=0.2) == RISK_REJECT


def test_business_decision_approve_boundary_is_tight():
    # Just below the tuned threshold approves; exactly at it reviews.
    assert business_decision(0.199999, approve_threshold=0.2) == RISK_APPROVE
    assert business_decision(0.2, approve_threshold=0.2) == RISK_REVIEW


if __name__ == "__main__":
    fns = [
        test_decision_buckets_below_approve,
        test_decision_bucket_review_mid,
        test_decision_bucket_reject_high,
        test_decision_boundary_approve,
        test_decision_for_probability_matches,
        test_business_decision_uses_tuned_threshold,
        test_business_decision_approve_boundary_is_tight,
        test_cost_matrix_fn_costlier,
        test_total_business_cost_weights,
        test_find_optimal_threshold_prefers_lower_cost_on_imbalanced_reject,
        test_find_optimal_threshold_returns_consistent_curve,
    ]
    for fn in fns:
        fn()
        print(f"  PASS: {fn.__name__}")
    print(f"\nAll {len(fns)} threshold tests passed.")