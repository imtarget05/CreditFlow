"""Evaluation functions for CreditFlow P5 — Model Evaluation.

Provides:
- compute_metrics: accuracy, precision, recall, f1, roc_auc
- calculate_confusion_matrix: raw confusion matrix
- summarize_model_scores: one-row summary per model

All logic is Colab-ready; owner controls execution. Local tests validate structure only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


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
        except ValueError:
            metrics["roc_auc"] = None  # single-class fold

    return metrics


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
