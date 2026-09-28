"""Boundary tests for the score -> decision cutoff (APPROVE / REVIEW / REJECT).

Import path (documented per task requirements):

* Decision function: ``pipeline.modeling.threshold.business_decision``
  (``probability, approve_threshold, review_max``).
* Display-bucket constants: ``DEFAULT_APPROVE_MAX`` (0.50) and
  ``DEFAULT_REVIEW_MAX`` (0.80) from the same module.
* REAL cutoff under test: the cost-tuned ``approve_threshold`` trained into
  ``models/production/meta.json`` (``"threshold": 0.2`` for the production
  logistic model), passed as ``approve_threshold`` to ``business_decision``
  by ``backend/predict_service.py::predict_risk``. There is NO ``0.20``
  Python constant in source — ``grep "0.20"`` only hits unrelated collateral
  haircuts / legal-rate caps / PSI drift bands. This test therefore LOADS the
  tuned value from ``meta.json`` and derives 0.1999 / 0.2000 / 0.2001 as
  ``tuned +/- 0.0001`` instead of hardcoding 0.20.

Boundary semantics found (strict ``<``, i.e. ``>=`` escalates):

    pipeline/modeling/threshold.py::business_decision:
        if probability < approve_threshold: return APPROVE   # strictly below
        if probability < review_max:        return REVIEW    # at cutoff -> REVIEW
        return REJECT                                        # >= review_max

  So a score EXACTLY at the cutoff yields REVIEW (not APPROVE). Note: the
  codebase spells the worst bucket ``REJECT`` (``RISK_REJECT``); there is no
  ``DECLINE`` string in source.

Stdlib-only constraint: ``pipeline/modeling/threshold.py`` does
``import numpy as np`` at module top, and the gateway venv used by the task
has NO numpy/sklearn/pandas. ``business_decision`` / ``assign_risk_level``
are pure comparisons that never touch numpy at call time, so this module
stubs a minimal ``numpy`` entry in ``sys.modules`` BEFORE the real import —
the lightest importable unit — keeping the test on the REAL source function
with zero new dependencies.
"""
from __future__ import annotations

import json
import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Lightest importable unit: stub numpy (module-top import only) so the
# --- REAL pipeline.modeling.threshold imports without numpy installed.
# --- business_decision/assign_risk_level never call numpy at runtime.
if "numpy" not in sys.modules:
    try:
        import numpy  # noqa: F401
    except ImportError:
        sys.modules["numpy"] = types.ModuleType("numpy")

from pipeline.modeling.threshold import (  # noqa: E402
    DEFAULT_REVIEW_MAX,
    RISK_APPROVE,
    RISK_REJECT,
    RISK_REVIEW,
    business_decision,
)

META_FILE = ROOT / "models" / "production" / "meta.json"


def _load_tuned_threshold() -> float:
    """REAL cutoff: cost-tuned threshold from the trained artifact."""
    meta = json.loads(META_FILE.read_text())
    return float(meta["threshold"])


TUNED_THRESHOLD = _load_tuned_threshold()

# Rank order for the monotonicity invariant (higher score never decides better).
_RANK = {RISK_APPROVE: 0, RISK_REVIEW: 1, RISK_REJECT: 2}

EPS = 0.0001  # with tuned=0.2 -> neighbours 0.1999 / 0.2001


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (-EPS, RISK_APPROVE),  # 0.1999 -> APPROVE (strictly below cutoff)
        (0.0, RISK_REVIEW),  # 0.2000 -> REVIEW (at cutoff, >= escalates)
        (+EPS, RISK_REVIEW),  # 0.2001 -> REVIEW (above cutoff)
    ],
)
def test_approve_cutoff_boundary(delta, expected):
    """Exact decision just below / at / just above the tuned cutoff."""
    score = TUNED_THRESHOLD + delta
    assert business_decision(score, TUNED_THRESHOLD) == expected, (
        f"score={score!r} (tuned={TUNED_THRESHOLD!r}, delta={delta}): "
        f"expected {expected}"
    )


def test_approve_boundary_is_strict_less_than():
    """Pin the >= vs > semantics: at-cutoff must NOT approve."""
    assert business_decision(TUNED_THRESHOLD - 1e-12, TUNED_THRESHOLD) == RISK_APPROVE
    assert business_decision(TUNED_THRESHOLD, TUNED_THRESHOLD) == RISK_REVIEW
    assert business_decision(TUNED_THRESHOLD + 1e-12, TUNED_THRESHOLD) == RISK_REVIEW


def test_literal_01999_02000_02001_when_tuned_is_02():
    """Guard the concrete triple from the task (valid when tuned == 0.2)."""
    assert abs(TUNED_THRESHOLD - 0.2) < 1e-12, (
        f"tuned threshold moved to {TUNED_THRESHOLD!r}; boundary triple "
        "must be re-derived as tuned +/- 0.0001"
    )
    assert business_decision(0.1999, TUNED_THRESHOLD) == RISK_APPROVE
    assert business_decision(0.2000, TUNED_THRESHOLD) == RISK_REVIEW
    assert business_decision(0.2001, TUNED_THRESHOLD) == RISK_REVIEW


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (-EPS, RISK_REVIEW),  # 0.7999 -> REVIEW
        (0.0, RISK_REJECT),  # 0.8000 -> REJECT (at review_max, >= escalates)
        (+EPS, RISK_REJECT),  # 0.8001 -> REJECT
    ],
)
def test_review_reject_boundary_at_review_max(delta, expected):
    """Second split (REVIEW vs REJECT) keeps the same strict-< semantics."""
    score = DEFAULT_REVIEW_MAX + delta
    assert business_decision(score, TUNED_THRESHOLD) == expected, (
        f"score={score!r} (review_max={DEFAULT_REVIEW_MAX!r}): expected {expected}"
    )


def test_monotonicity_seeded_sweep():
    """Higher score never yields a better (lower-rank) decision."""
    rng = random.Random(20260927)
    scores = [rng.uniform(0.0, 1.0) for _ in range(500)]
    # Always include the exact boundary neighbourhood in the sweep.
    scores += [
        TUNED_THRESHOLD - EPS,
        TUNED_THRESHOLD,
        TUNED_THRESHOLD + EPS,
        DEFAULT_REVIEW_MAX - EPS,
        DEFAULT_REVIEW_MAX,
        DEFAULT_REVIEW_MAX + EPS,
        0.0,
        1.0,
    ]
    ranked = sorted(
        (_RANK[business_decision(s, TUNED_THRESHOLD)], s) for s in scores
    )
    # Scores sorted ascending must have non-decreasing decision ranks; walk
    # the score order directly to assert the invariant.
    ordered = sorted(scores)
    prev_rank = -1
    for s in ordered:
        rank = _RANK[business_decision(s, TUNED_THRESHOLD)]
        assert rank >= prev_rank, (
            f"monotonicity violated: score {s!r} ranks {rank} after {prev_rank}"
        )
        prev_rank = rank
    assert ranked[0][0] == _RANK[RISK_APPROVE]
    assert ranked[-1][0] == _RANK[RISK_REJECT]
