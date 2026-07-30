"""95% Beta credible interval, computed in pure Python (no scipy/numpy dependency).

The interval ``[ci_lower, ci_upper]`` for ``Beta(alpha, beta)`` is found by inverting the
regularized incomplete beta function ``I_x(a, b)`` at the 0.025 and 0.975 quantiles. ``I_x`` is
evaluated with the Lentz continued-fraction expansion (the classic Numerical Recipes
``betai``/``betacf`` method) and inverted by bisection. Because the service floors
``alpha``/``beta`` at 1.0, both shape
parameters are always >= 1.0 and the numerically delicate region near 0 never occurs.

The result matches ``scipy.stats.beta.interval`` to well within the 4-decimal tolerance the audit
trail stores and the tests assert (see tests/test_history.py, which pins known scipy values).
"""

from __future__ import annotations

import math

# Bisection is iterated until the x-interval is narrower than this; ~1e-12 far exceeds the
# 4-decimal precision we persist, and each iteration halves the error so the loop is short.
_TOLERANCE = 1e-12
_MAX_ITERATIONS = 200


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's algorithm)."""
    tiny = 1e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, _MAX_ITERATIONS + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Return ``I_x(a, b)`` (the Beta CDF at ``x``), for ``a, b > 0`` and ``0 <= x <= 1``."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log1p(-x) * b - ln_beta) / a
    # The continued fraction converges fast on the appropriate side of the symmetry point.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x)
    ln_beta_sym = math.lgamma(b) + math.lgamma(a) - math.lgamma(a + b)
    front_sym = math.exp(math.log(x) * a + math.log1p(-x) * b - ln_beta_sym) / b
    return 1.0 - front_sym * _betacf(b, a, 1.0 - x)


def _beta_ppf(p: float, a: float, b: float) -> float:
    """Inverse Beta CDF (quantile) at probability ``p`` via bisection on ``I_x(a, b) = p``."""
    lo, hi = 0.0, 1.0
    for _ in range(_MAX_ITERATIONS):
        mid = 0.5 * (lo + hi)
        if regularized_incomplete_beta(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < _TOLERANCE:
            break
    return 0.5 * (lo + hi)


def beta_credible_interval(
    alpha: float, beta: float, confidence: float = 0.95
) -> tuple[float, float]:
    """Return the equal-tailed ``confidence`` credible interval ``[lower, upper]`` for Beta(a, b).

    Equivalent to ``scipy.stats.beta.interval(confidence, alpha, beta)``. Requires
    ``alpha, beta > 0`` (always true here — the service floors both at 1.0).
    """
    if alpha <= 0.0 or beta <= 0.0:
        raise ValueError("alpha and beta must be positive to form a Beta credible interval")
    tail = (1.0 - confidence) / 2.0
    lower = _beta_ppf(tail, alpha, beta)
    upper = _beta_ppf(1.0 - tail, alpha, beta)
    return lower, upper
