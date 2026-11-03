"""Evaluation functions for CreditFlow P5 — Model Evaluation.

Provides:
- compute_metrics: classification, ranking, and probability-calibration metrics
- calculate_confusion_matrix: raw confusion matrix
- summarize_model_scores: one-row summary per model

All logic is Colab-ready; owner controls execution. Local tests validate structure only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.modeling.threshold import DEFAULT_REVIEW_MAX


def compute_metrics(y_true, y_pred, y_proba=None) -> dict:
    """Compute core classification metrics.

    Parameters
    ----------
    y_true : array-like
        True labels (0/1).
    y_pred : array-like
        Predicted labels (0/1).
    y_proba : array-like, optional
        Predicted probabilities for positive class (for ROC-AUC).

    Returns
    -------
    dict
        {"accuracy", "precision", "recall", "f1", "roc_auc"}.
 roc_auc is None if not computable.

    """
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        f1_score,
        roc_auc_score,
        average_precision_score,
        brier_score_loss,
        roc_curve,
    )

    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }

    if y_proba is not None:
        try:
            metrics["roc_auc"] = float(roc_auc_score(y_true, y_proba))
            metrics["pr_auc"] = float(average_precision_score(y_true, y_proba))
            fpr, tpr, _ = roc_curve(y_true, y_proba)
            metrics["ks"] = float(np.max(tpr - fpr))
            metrics["brier_score"] = float(brier_score_loss(y_true, y_proba))
            metrics["expected_calibration_error"] = expected_calibration_error(
                y_true, y_proba
            )
        except ValueError:
            metrics["roc_auc"] = None  # single-class fold
            metrics["pr_auc"] = None
            metrics["ks"] = None
            metrics["brier_score"] = None
            metrics["expected_calibration_error"] = None

    return metrics


def expected_calibration_error(y_true, y_proba, n_bins: int = 10) -> float | None:
    """Return equal-width, sample-weighted expected calibration error (ECE).

    ECE is a compact diagnostic, not a calibration proof. The caller should
    report the bin table and Brier score alongside it, especially on small or
    synthetic evaluation sets.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_proba, dtype=float)
    if y.size == 0 or y.size != p.size or n_bins < 1:
        return None
    if not np.isfinite(y).all() or not np.isfinite(p).all():
        return None
    p = np.clip(p, 0.0, 1.0)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for index in range(n_bins):
        mask = (p >= edges[index]) & (
            p <= edges[index + 1] if index == n_bins - 1 else p < edges[index + 1]
        )
        if mask.any():
            ece += float(mask.mean()) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(ece)


def calibration_bins(y_true, y_proba, n_bins: int = 10) -> list[dict]:
    """Return non-empty equal-width probability bins for a calibration plot/table."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_proba, dtype=float)
    if y.size == 0 or y.size != p.size or n_bins < 1:
        return []
    if not np.isfinite(y).all() or not np.isfinite(p).all():
        return []
    p = np.clip(p, 0.0, 1.0)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for index in range(n_bins):
        mask = (p >= edges[index]) & (
            p <= edges[index + 1] if index == n_bins - 1 else p < edges[index + 1]
        )
        if mask.any():
            rows.append({
                "lower": float(edges[index]),
                "upper": float(edges[index + 1]),
                "count": int(mask.sum()),
                "mean_predicted_probability": float(p[mask].mean()),
                "observed_default_rate": float(y[mask].mean()),
            })
    return rows


def threshold_operating_points(
    y_true,
    y_proba,
    approval_thresholds=(0.15, 0.20, 0.25, 0.30),
    reject_threshold: float = DEFAULT_REVIEW_MAX,
) -> list[dict]:
    """Summarize three-way approve/review/reject outcomes at candidate cutoffs.

    The policy form is ``p < approval_threshold → APPROVE``,
    ``approval_threshold ≤ p < reject_threshold → REVIEW``, and
    ``p ≥ reject_threshold → REJECT``. These are descriptive outcomes; they do
    not assign monetary utility to review or imply the synthetic policy is
    appropriate for a lender.
    """
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(y_proba, dtype=float)
    if y.size == 0 or y.size != p.size:
        return []
    points = []
    for cutoff in approval_thresholds:
        approve = p < cutoff
        review = (p >= cutoff) & (p < reject_threshold)
        reject = p >= reject_threshold

        def bucket(mask):
            count = int(mask.sum())
            defaults = int(y[mask].sum()) if count else 0
            return {
                "count": count,
                "rate": float(count / len(y)),
                "observed_default_rate": float(defaults / count) if count else None,
            }

        rejected_defaults = int(y[reject].sum())
        total_defaults = int(y.sum())
        points.append({
            "approval_threshold": float(cutoff),
            "reject_threshold": float(reject_threshold),
            "approve": bucket(approve),
            "review": bucket(review),
            "reject": bucket(reject),
            "default_capture_rate_in_reject": (
                float(rejected_defaults / total_defaults) if total_defaults else None
            ),
            "n": int(len(y)),
        })
    return points


def calculate_confusion_matrix(y_true, y_pred) -> np.ndarray:
    """Return a confusion matrix (2x2) where rows=actual, cols=predicted."""
    from sklearn.metrics import confusion_matrix
    return confusion_matrix(y_true, y_pred)


def build_summary_row(
    model_name: str,
    metrics: dict,
    best_threshold: float | None = None,
    best_cost: float | None = None,
) -> dict:
    """Build a one-row summary dict for a model.**
    """
    row = {"model": model_name}
    row.update(metrics)
    if best_threshold is not None:
        row["best_threshold"] = best_threshold
    if best_cost is not None:
        row["best_cost"] = best_cost
    return row


def metrics_table(rows: list[dict]) -> pd.DataFrame:
    """Turn a list of summary rows into a sorted DataFrame."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "f1" in df.columns:
        df = df.sort_values("f1", ascending=False).reset_index(drop=True)
    return df
