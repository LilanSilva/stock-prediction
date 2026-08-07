"""Unit tests for the pure Beta-Bernoulli credit math."""

from __future__ import annotations

import pytest
from shared.schemas.messages import ContributingEdge, Direction

from credibility.updater import (
    apply_bernoulli,
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
