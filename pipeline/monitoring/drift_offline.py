"""Offline drift primitives on plain float lists (stdlib only).

Companion to :mod:`pipeline.monitoring.drift` (which is pandas/numpy-based
and already wired to ``GET /drift`` in ``backend/app.py``). This module exists
for environments without numpy/pandas (minimal CI venvs, offline jobs): it
operates on plain ``list[float]`` using only the standard library.

Provided:

* :func:`psi_equal_width` — Population Stability Index with equal-width bins
  over the combined range, epsilon-smoothed. ``0.0`` for identical inputs;
  large for shifted distributions (industry bands: <0.10 no change,
  0.10–0.25 watch, >0.25 significant shift).
* :func:`ks_statistic` — two-sample Kolmogorov–Smirnov statistic in [0, 1].

Both raise :class:`ValueError` on empty input. Both are pure/deterministic:
same inputs always give the same value.

Phase-8 wiring one-liner (NOT done here — ``backend/app.py`` already imports
pandas, so wiring this module in would not keep imports light; the existing
``GET /drift`` endpoint stays the serving path)::

    from pipeline.monitoring.drift_offline import psi_equal_width, ks_statistic

Limitation (same as the main module): this reports data/prediction
distribution drift only — NOT model performance degradation, which requires
ground-truth labels unavailable at inference time.
"""

from __future__ import annotations

import math

PSI_WATCH = 0.10
PSI_DRIFT = 0.25


def _as_floats(values: list[float], name: str) -> list[float]:
    try:
        out = [float(v) for v in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a list of numbers") from exc
    if not out:
        raise ValueError(f"{name} must not be empty")
    for v in out:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"{name} must not contain NaN or inf")
    return out


def psi_equal_width(
    expected: list[float],
    actual: list[float],
    bins: int = 10,
    epsilon: float = 1e-6,
) -> float:
    """Population Stability Index between two plain float lists.

    Equal-width bins span the combined ``[min, max]`` of both samples; bin
    proportions below *epsilon* are floored at *epsilon* (standard smoothing
    so empty bins stay finite). Returns a float >= 0 (``0.0`` for identical
    inputs). Raises :class:`ValueError` if either input is empty (or holds
    NaN/inf), or if *bins* < 1.
    """
    if bins < 1:
        raise ValueError("bins must be >= 1")
    exp = _as_floats(expected, "expected")
    act = _as_floats(actual, "actual")
    lo = min(min(exp), min(act))
    hi = max(max(exp), max(act))
    if hi <= lo:
        # Degenerate range (all values identical): drift iff the constants differ.
        return 0.0 if exp[0] == act[0] else float("inf")
    width = (hi - lo) / bins

    def _counts(xs: list[float]) -> list[int]:
        counts = [0] * bins
        for x in xs:
            idx = int((x - lo) / width)
            if idx >= bins:
                idx = bins - 1  # x == hi lands in the last bin
            counts[idx] += 1
        return counts

    n_e, n_a = len(exp), len(act)
    total = 0.0
    for ce, ca in zip(_counts(exp), _counts(act)):
        pe = max(ce / n_e, epsilon)
        pa = max(ca / n_a, epsilon)
        total += (pa - pe) * math.log(pa / pe)
    return total


def _ecdf(sorted_xs: list[float], x: float) -> float:
    """P(X <= x) for an already-sorted sample (bisection, stdlib)."""
    lo, hi = 0, len(sorted_xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_xs[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_xs)


def ks_statistic(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov–Smirnov statistic in [0, 1].

    Maximum absolute difference between the empirical CDFs of two plain
    float lists. ``0.0`` for identical inputs, ``1.0`` for fully separated
    ones. Raises :class:`ValueError` if either input is empty (or holds
    NaN/inf).
    """
    xs = sorted(_as_floats(a, "a"))
    ys = sorted(_as_floats(b, "b"))
    worst = 0.0
    for x in xs + ys:
        diff = abs(_ecdf(xs, x) - _ecdf(ys, x))
        if diff > worst:
            worst = diff
    return worst
