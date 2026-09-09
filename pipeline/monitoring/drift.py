"""ML drift monitoring for CreditFlow (P11 — minimal production-oriented version).

Detects distribution shift between the training/reference data and a recent
inference window:

    reference statistics (written at training time)
            ↓
    recent production window (collected by the API)
            ↓
    drift calculation (PSI per numeric feature + prediction distribution)
            ↓
    status: NO_DRIFT | DRIFT_DETECTED | INSUFFICIENT_DATA

Important distinction (documented, not hidden):
    DATA DRIFT ≠ MODEL PERFORMANCE DEGRADATION.
    True performance degradation requires ground-truth labels for the recent
    window, which are not available at inference time in this system. This
    module therefore reports *data/prediction distribution drift only*; it makes
    no claim about model quality degradation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Statuses
NO_DRIFT = "NO_DRIFT"
DRIFT_DETECTED = "DRIFT_DETECTED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# PSI interpretation bands (standard industry rule of thumb):
#   PSI < 0.10  -> no significant change
#   0.10–0.20   -> moderate shift (worth watching)
#   > 0.20      -> significant shift
PSI_WARN = 0.10
PSI_DRIFT = 0.20


def compute_reference_stats(df: pd.DataFrame, features: list[str]) -> dict:
    """Compute reference statistics per feature (mean/std/quantile bins/weights).

    Called at training time on the training data; persisted to
    models/production/reference_stats.json so serving/monitoring compares
    against exactly what the model was trained on. ``weights`` holds the true
    reference bin proportions (needed for discrete features where quantile
    edges collapse and equal-mass no longer holds).
    """
    stats: dict = {"n_rows": int(len(df)), "features": {}}
    for f in features:
        col = pd.to_numeric(df[f], errors="coerce").dropna()
        if col.empty:
            continue
        q = np.linspace(0, 1, 11)
        edges = np.unique(np.quantile(col, q))
        if edges.size >= 2:
            idx = np.clip(np.searchsorted(edges, col.to_numpy(), side="right") - 1,
                          0, edges.size - 2)
            counts = np.bincount(idx, minlength=edges.size - 1).astype(float)
            weights = (counts / counts.sum()).tolist()
        else:
            weights = [1.0]
        stats["features"][f] = {
            "mean": float(col.mean()),
            "std": float(col.std(ddof=0)),
            "bins": [float(x) for x in edges],
            "weights": weights,
            "n": int(col.count()),
        }
    return stats


def compute_prediction_reference(proba: np.ndarray) -> dict:
    """Reference distribution for the model's risk probabilities (validation set)."""
    p = np.asarray(proba, dtype=float)
    q = np.linspace(0, 1, 11)
    edges = np.unique(np.quantile(p, q))
    if edges.size >= 2:
        idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, edges.size - 2)
        counts = np.bincount(idx, minlength=edges.size - 1).astype(float)
        weights = (counts / counts.sum()).tolist()
    else:
        weights = [1.0]
    return {
        "mean": float(p.mean()),
        "std": float(p.std(ddof=0)),
        "bins": [float(x) for x in edges],
        "weights": weights,
        "n": int(p.size),
    }


def psi(expected: list[float] | np.ndarray, actual: list[float] | np.ndarray,
        weights: list[float] | np.ndarray | None = None,
        eps: float = 1e-6) -> float:
    """Population Stability Index between two distributions.

    ``expected`` = bin edges (quantiles) from the reference distribution;
    ``actual`` = recent sample values; ``weights`` = true reference bin
    proportions (falls back to uniform for equal-mass quantile bins).
    Returns total PSI (float >= 0).
    """
    edges = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    a = a[~np.isnan(a)]
    if a.size == 0 or edges.size < 2:
        return float("nan")

    # Deduplicate monotone edges (features with heavy ties can produce
    # identical quantile boundaries) so np.searchsorted stays valid.
    uniq = np.unique(edges)
    if uniq.size < 2:
        # Degenerate reference (constant feature): drift iff recent mean differs.
        return 0.0 if abs(float(a.mean()) - float(edges[0])) < 1e-12 else float("inf")

    # Bin the recent sample using reference quantile edges.
    idx = np.searchsorted(uniq, a, side="right") - 1
    idx = np.clip(idx, 0, uniq.size - 2)
    counts = np.bincount(idx, minlength=uniq.size - 1).astype(float)
    n_bins = uniq.size - 1
    if weights is not None and len(weights) == n_bins:
        expected_pct = np.clip(np.asarray(weights, dtype=float), eps, None)
    else:
        expected_pct = np.full(n_bins, 1.0 / n_bins)
    expected_pct = np.clip(expected_pct, eps, None)
    actual_pct = np.clip(counts / max(counts.sum(), 1), eps, None)
    return float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))


def _status_for(value: float) -> str:
    if value > PSI_DRIFT:
        return DRIFT_DETECTED
    if value > PSI_WARN:
        return "WATCH"
    return NO_DRIFT


def detect_drift(
    reference: dict,
    recent_df: pd.DataFrame,
    min_samples: int = 50,
) -> dict:
    """Compare a recent inference window against the reference statistics.

    Returns a report dict:
      status          NO_DRIFT | DRIFT_DETECTED | INSUFFICIENT_DATA
      n_recent        size of the recent window
      features        {feature: {psi, status}}
      prediction      {psi, status} for risk_probability (if present)
      note            limitation statement (data drift ≠ performance degradation)
    """
    n = int(len(recent_df))
    report: dict = {
        "status": INSUFFICIENT_DATA,
        "n_recent": n,
        "min_samples": min_samples,
        "features": {},
        "prediction": {},
        "note": (
            "Data/prediction distribution drift only — this is NOT a model "
            "performance degradation claim (that requires ground-truth labels "
            "for the recent window, which are unavailable at inference time)."
        ),
    }
    if n < min_samples or not reference.get("features"):
        return report

    worst = 0.0
    for feat, fstats in reference["features"].items():
        if feat not in recent_df.columns:
            continue
        val = psi(fstats["bins"], recent_df[feat].to_numpy(), fstats.get("weights"))
        if np.isnan(val):
            continue
        report["features"][feat] = {"psi": round(val, 4), "status": _status_for(val)}
        if val == float("inf"):
            worst = float("inf")
        else:
            worst = max(worst, val)

    if "risk_probability" in recent_df.columns and reference.get("prediction"):
        val = psi(
            reference["prediction"]["bins"],
            recent_df["risk_probability"].to_numpy(),
            reference["prediction"].get("weights"),
        )
        if not np.isnan(val):
            report["prediction"] = {"psi": round(val, 4), "status": _status_for(val)}
            if val == float("inf"):
                worst = float("inf")
            else:
                worst = max(worst, val)

    report["status"] = DRIFT_DETECTED if worst > PSI_DRIFT else NO_DRIFT
    return report
