"""Tests for pipeline/monitoring/drift_offline.py (stdlib + pytest only).

Seeded-random invariants on plain float lists. Does NOT touch the existing
tests/test_drift.py (pandas/numpy path) — this covers the offline module only.
"""

from __future__ import annotations

import random

import pytest

from pipeline.monitoring.drift_offline import ks_statistic, psi_equal_width

N_SEEDS = 25


def _rng(seed: int) -> random.Random:
    return random.Random(7_000 + seed)


def _gauss(rng: random.Random, mu: float, sigma: float, n: int) -> list[float]:
    return [rng.gauss(mu, sigma) for _ in range(n)]


# ---------------------------------------------------------------- PSI invariants

@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_psi_identical_distribution_is_zero(seed):
    rng = _rng(seed)
    sample = _gauss(rng, 0.0, 1.0, 800)
    assert psi_equal_width(sample, list(sample)) == 0.0


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_psi_same_distribution_different_draws_is_small(seed):
    rng = _rng(seed)
    ref = _gauss(rng, 0.0, 1.0, 2000)
    cur = _gauss(rng, 0.0, 1.0, 2000)
    assert psi_equal_width(ref, cur) < 0.10


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_psi_shifted_distribution_is_large(seed):
    rng = _rng(seed)
    ref = _gauss(rng, 0.0, 1.0, 2000)
    shifted = _gauss(rng, 3.0, 1.0, 2000)
    assert psi_equal_width(ref, shifted) > 0.25


def test_psi_is_nonnegative_and_symmetric_inputs():
    rng = _rng(99)
    a = _gauss(rng, 0.0, 1.0, 500)
    b = _gauss(rng, 1.5, 1.0, 500)
    assert psi_equal_width(a, b) >= 0.0
    # PSI is symmetric by construction (same bins, symmetric sum).
    assert psi_equal_width(a, b) == pytest.approx(psi_equal_width(b, a))


@pytest.mark.parametrize("bad_args", [([], [1.0]), ([1.0], []), ([], [])])
def test_psi_empty_input_raises_value_error(bad_args):
    with pytest.raises(ValueError):
        psi_equal_width(*bad_args)


@pytest.mark.parametrize("bad_args", [([float("nan")], [1.0]), ([1.0], [float("inf")])])
def test_psi_nonfinite_input_raises_value_error(bad_args):
    with pytest.raises(ValueError):
        psi_equal_width(*bad_args)


def test_psi_rejects_bad_bins():
    with pytest.raises(ValueError):
        psi_equal_width([1.0, 2.0], [1.0, 2.0], bins=0)


# ---------------------------------------------------------------- KS invariants

@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_ks_in_unit_interval(seed):
    rng = _rng(seed)
    a = _gauss(rng, 0.0, 1.0, 300)
    b = _gauss(rng, rng.choice([0.0, 2.0]), 1.0, 300)
    ks = ks_statistic(a, b)
    assert 0.0 <= ks <= 1.0


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_ks_identical_inputs_are_zero(seed):
    rng = _rng(seed)
    sample = _gauss(rng, 0.0, 1.0, 300)
    assert ks_statistic(sample, list(sample)) == 0.0


def test_ks_separated_inputs_are_one():
    assert ks_statistic([1.0, 2.0, 3.0], [10.0, 11.0, 12.0]) == 1.0


@pytest.mark.parametrize("bad_args", [([], [1.0]), ([1.0], []), ([], [])])
def test_ks_empty_input_raises_value_error(bad_args):
    with pytest.raises(ValueError):
        ks_statistic(*bad_args)


# ---------------------------------------------------------------- determinism

@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_determinism_same_seed_same_values(seed):
    r1, r2 = _rng(seed), _rng(seed)
    ref1, ref2 = _gauss(r1, 0.0, 1.0, 500), _gauss(r2, 0.0, 1.0, 500)
    cur1 = _gauss(r1, 2.0, 1.0, 500)
    cur2 = _gauss(r2, 2.0, 1.0, 500)
    assert ref1 == ref2 and cur1 == cur2  # same seed -> same draws
    assert psi_equal_width(ref1, cur1) == psi_equal_width(ref2, cur2)
    assert ks_statistic(ref1, cur1) == ks_statistic(ref2, cur2)
