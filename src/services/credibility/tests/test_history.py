"""Unit tests for the pure-python Beta credible interval.

Reference values are taken from ``scipy.stats.beta.interval(0.95, a, b)`` and the Beta CDF from
``scipy.stats.beta.cdf``; the pure-python implementation must match them to 4 decimal places, which
is the precision the audit trail stores.
"""

from __future__ import annotations

import pytest

from credibility.history import beta_credible_interval, regularized_incomplete_beta


def test_cdf_matches_known_values() -> None:
    # scipy.stats.beta.cdf(0.5, 2, 2) == 0.5 by symmetry.
    assert regularized_incomplete_beta(2.0, 2.0, 0.5) == pytest.approx(0.5, abs=1e-6)
    # scipy.stats.beta.cdf(0.5, 5, 3) == 0.226562...
    assert regularized_incomplete_beta(5.0, 3.0, 0.5) == pytest.approx(0.2265625, abs=1e-6)


def test_cdf_boundaries() -> None:
    assert regularized_incomplete_beta(3.0, 4.0, 0.0) == 0.0
    assert regularized_incomplete_beta(3.0, 4.0, 1.0) == 1.0


def test_uniform_prior_interval() -> None:
    # scipy.stats.beta.interval(0.95, 1, 1) == (0.025, 0.975).
    lower, upper = beta_credible_interval(1.0, 1.0)
    assert lower == pytest.approx(0.025, abs=1e-4)
    assert upper == pytest.approx(0.975, abs=1e-4)


def test_symmetric_interval() -> None:
    # scipy.stats.beta.interval(0.95, 5, 5) == (0.2120, 0.7880) approx; symmetric about 0.5.
    lower, upper = beta_credible_interval(5.0, 5.0)
    assert lower == pytest.approx(0.2120, abs=1e-4)
    assert upper == pytest.approx(0.7880, abs=1e-4)
    assert (lower + upper) == pytest.approx(1.0, abs=1e-4)


def test_beta_5_3_interval_matches_scipy() -> None:
    # Equal-tailed interval: CDF(lower)=0.025, CDF(upper)=0.975 (verified below and self-consistent
    # with regularized_incomplete_beta). scipy.stats.beta.interval(0.95, 5, 3) == (0.2904, 0.9010).
    lower, upper = beta_credible_interval(5.0, 3.0)
    assert lower == pytest.approx(0.2904, abs=1e-4)
    assert upper == pytest.approx(0.9010, abs=1e-4)
    assert regularized_incomplete_beta(5.0, 3.0, lower) == pytest.approx(0.025, abs=1e-6)
    assert regularized_incomplete_beta(5.0, 3.0, upper) == pytest.approx(0.975, abs=1e-6)


def test_mature_interval_is_narrow() -> None:
    # scipy.stats.beta.interval(0.95, 50, 10) == (0.7301, 0.9156).
    lower, upper = beta_credible_interval(50.0, 10.0)
    assert lower == pytest.approx(0.7301, abs=1e-4)
    assert upper == pytest.approx(0.9156, abs=1e-4)
    assert 0.0 <= lower < upper <= 1.0


def test_rejects_non_positive_parameters() -> None:
    with pytest.raises(ValueError):
        beta_credible_interval(0.0, 1.0)
    with pytest.raises(ValueError):
        beta_credible_interval(1.0, -1.0)
