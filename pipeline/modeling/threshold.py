"""Threshold selection & decision logic for CreditFlow — Decision Engineering (P5).

Maps a risk-probability to a business bucket (APPROVE / REVIEW / REJECT) using
configurable thresholds, and finds the optimal classification threshold by
minimising a business cost matrix where FN (missed default) costs more than
FP (rejected good customer) — per spec §3/§9.
"""
from __future__ import annotations

import numpy as np

# Default thresholds from spec section 4.4
DEFAULT_APPROVE_MAX = 0.50
DEFAULT_REVIEW_MAX = 0.80

# Risk levels / decisions
RISK_APPROVE = "APPROVE"
RISK_REVIEW = "REVIEW"
RISK_REJECT = "REJECT"

# Baseline business cost: FN cost > FP cost (spec section 9)
DEFAULT_FN_COST = 5.0
DEFAULT_FP_COST = 1.0


def cost_matrix(fn_cost: float = DEFAULT_FN_COST, fp_cost: float = DEFAULT_FP_COST) -> dict:
    """Return business cost matrix (cost per confusion cell)."""
    return {
        (0, 0): 0.0,       # TN: correct approve
        (0, 1): fp_cost,   # FP: rejected good customer
        (1, 0): fn_cost,   # FN: missed default (expensive)
        (1, 1): 0.0,       # TP: correct reject/review
    }


def total_business_cost(
    y_true, y_pred, fn_cost: float = DEFAULT_FN_COST, fp_cost: float = DEFAULT_FP_COST
) -> float:
    """Total normalized cost = FN_count * fn_cost + FP_count * fp_cost."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)

    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())

    return float(fn * fn_cost + fp * fp_cost)


def assign_risk_level(
    risk_probability: float,
    approve_max: float = DEFAULT_APPROVE_MAX,
    review_max: float = DEFAULT_REVIEW_MAX,
) -> str:
    """Assign a risk bucket: APPROVE / REVIEW / REJECT."""
    if risk_probability < approve_max:
        return RISK_APPROVE
    if risk_probability < review_max:
        return RISK_REVIEW
    return RISK_REJECT


def decision_for_probability(
    risk_probability: float,
    approve_max: float = DEFAULT_APPROVE_MAX,
    review_max: float = DEFAULT_REVIEW_MAX,
) -> str:
    """Return the business decision for a probability."""
    return assign_risk_level(risk_probability, approve_max, review_max)


def business_decision(
    probability: float,
    approve_threshold: float,
    review_max: float = DEFAULT_REVIEW_MAX,
) -> str:
    """Business decision using the cost-tuned approve threshold (Phase 4).

    The tuned threshold (found by ``find_optimal_threshold`` during training,
    e.g. ~0.2 for logistic) decides APPROVE vs REVIEW/REJECT; the REVIEW vs
    REJECT split stays on ``review_max``. This is deliberately separate from
    ``assign_risk_level``, which keeps the fixed display buckets
    (LOW/MEDIUM/HIGH by 0.5 / 0.8).
    """
    if probability < approve_threshold:
        return RISK_APPROVE
    if probability < review_max:
        return RISK_REVIEW
    return RISK_REJECT


def find_optimal_threshold(
    y_true,
    y_proba,
    fn_cost: float = DEFAULT_FN_COST,
    fp_cost: float = DEFAULT_FP_COST,
    thresholds=None,
) -> dict:
    """Find threshold minimising total business cost (cost-sensitive).

    Returns a dict with best_threshold, best_cost, the full threshold/cost curves,
    and the recall achieved at the optimal threshold.
    """
    from sklearn.metrics import recall_score

    y_true = np.asarray(y_true)
    y_proba = np.asarray(y_proba)

    if thresholds is None:
        thresholds = np.linspace(0.05, 0.95, 19)

    costs = []
    for t in thresholds:
        y_pred = (y_proba >= t).astype(int)
        costs.append(total_business_cost(y_true, y_pred, fn_cost, fp_cost))

    costs = np.asarray(costs)
    best_idx = int(np.argmin(costs))

    best_pred = (y_proba >= thresholds[best_idx]).astype(int)
    best_recall = float(recall_score(y_true, best_pred, zero_division=0))

    return {
        "best_threshold": float(thresholds[best_idx]),
        "best_cost": float(costs[best_idx]),
        "thresholds": thresholds.tolist(),
        "costs": costs.tolist(),
        "recall_at_best": best_recall,
    }