"""Unit tests for the pure Beta-Bernoulli credit math."""

from __future__ import annotations

import pytest
from shared.schemas.messages import ContributingEdge, Direction

from credibility.updater import (
    apply_bernoulli,
    apply_weight_delta,
    compute_proportional_credits,
    compute_source_credits,
)


def _edge(edge_id: str, influence: float) -> ContributingEdge:
    return ContributingEdge(
        edge_id=edge_id,
        direction=Direction.UP,
        current_weight=0.5,
        influence_weight=influence,
        path=edge_id,
    )


def test_proportional_credits_normalise_to_one() -> None:
    edges = [_edge("MILITARY_CONFLICT->NEM_NYSE", 0.7), _edge("INFLATION_CHANGE->NEM_NYSE", 0.3)]
    credits = compute_proportional_credits(edges)
    assert credits["MILITARY_CONFLICT->NEM_NYSE"] == pytest.approx(0.7)
    assert credits["INFLATION_CHANGE->NEM_NYSE"] == pytest.approx(0.3)
    assert sum(credits.values()) == pytest.approx(1.0)


def test_proportional_credits_single_edge_gets_full_credit() -> None:
    credits = compute_proportional_credits([_edge("SANCTIONS->XOM_NYSE", 0.2)])
    assert credits["SANCTIONS->XOM_NYSE"] == pytest.approx(1.0)


def test_proportional_credits_zero_sum_falls_back_to_equal() -> None:
    edges = [_edge("SANCTIONS->NEM_NYSE", 0.0), _edge("RATE_DECISION->NEM_NYSE", 0.0)]
    credits = compute_proportional_credits(edges)
    assert credits["SANCTIONS->NEM_NYSE"] == pytest.approx(0.5)
    assert credits["RATE_DECISION->NEM_NYSE"] == pytest.approx(0.5)


def test_proportional_credits_empty_list() -> None:
    assert compute_proportional_credits([]) == {}


def test_source_credits_equal_split() -> None:
    credits = compute_source_credits(["di.se", "svd.se"])
    assert credits == {"di.se": pytest.approx(0.5), "svd.se": pytest.approx(0.5)}


def test_source_credits_single_source() -> None:
    assert compute_source_credits(["aftonbladet.se"]) == {"aftonbladet.se": pytest.approx(1.0)}


def test_source_credits_lowercases_domains() -> None:
    credits = compute_source_credits(["DI.SE", " SvD.se "])
    assert set(credits) == {"di.se", "svd.se"}


def test_source_credits_empty_list() -> None:
    assert compute_source_credits([]) == {}


def test_apply_bernoulli_hit_adds_to_alpha() -> None:
    alpha, beta = apply_bernoulli(1.0, 1.0, 0.7, is_correct=True, floor=1.0)
    assert alpha == pytest.approx(1.7)
    assert beta == pytest.approx(1.0)


def test_apply_bernoulli_miss_adds_to_beta() -> None:
    alpha, beta = apply_bernoulli(1.0, 1.0, 0.3, is_correct=False, floor=1.0)
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(1.3)


def test_apply_bernoulli_enforces_floor() -> None:
    # Seed values below the floor are corrected upward on first touch.
    alpha, beta = apply_bernoulli(0.0, 0.5, 0.2, is_correct=True, floor=1.0)
    assert alpha == pytest.approx(1.0)  # 0.0 + 0.2 = 0.2, floored to 1.0
    assert beta == pytest.approx(1.0)  # 0.5 floored to 1.0


# --- KG edge weight (outcome-driven) ---


def test_apply_weight_delta_hit_raises_weight() -> None:
    assert apply_weight_delta(
        0.40, 1.0, is_correct=True, step=0.02, floor=0.05
    ) == pytest.approx(0.42)


def test_apply_weight_delta_miss_lowers_weight() -> None:
    assert apply_weight_delta(
        0.40, 1.0, is_correct=False, step=0.02, floor=0.05
    ) == pytest.approx(0.38)


def test_apply_weight_delta_scales_by_credit_share() -> None:
    # An edge that contributed a quarter of the decision earns a quarter of the move.
    assert apply_weight_delta(
        0.40, 0.25, is_correct=True, step=0.02, floor=0.05
    ) == pytest.approx(0.405)


def test_apply_weight_delta_clamps_at_floor() -> None:
    assert apply_weight_delta(
        0.06, 1.0, is_correct=False, step=0.02, floor=0.05
    ) == pytest.approx(0.05)


def test_apply_weight_delta_clamps_at_ceiling() -> None:
    assert apply_weight_delta(
        0.99, 1.0, is_correct=True, step=0.02, floor=0.05
    ) == pytest.approx(1.0)


def test_apply_weight_delta_never_reaches_zero_or_flips() -> None:
    # An edge asserts a causal relationship, so sustained bad outcomes must make it negligible
    # rather than delete it or let it change sign.
    weight = 0.45
    for _ in range(200):
        weight = apply_weight_delta(weight, 1.0, is_correct=False, step=0.02, floor=0.05)
    assert weight == pytest.approx(0.05)
    assert weight > 0.0
