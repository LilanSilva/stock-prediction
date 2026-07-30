"""Pure Beta-Bernoulli credit math (no I/O), unit-tested in isolation.

Two credit-assignment rules, matching the frozen ``PredictionScored`` contract:

  - Edges carry an explicit ``influence_weight``; credit is proportional to each edge's share of the
    total influence in the message (``compute_proportional_credits``).
  - Sources are a plain list of domain strings; credit is split equally across them
    (``compute_source_credits``).

On a hit the credit is added to ``alpha`` (successes); on a miss it is added to ``beta`` (failures).
``alpha``/``beta`` are floored at the uninformed prior so a seeded edge can only ever grow.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.schemas.messages import ContributingEdge


@dataclass(frozen=True)
class WeightUpdate:
    """A single entity's Beta-Bernoulli transition, captured for persistence and history."""

    entity_id: str
    entity_type: str  # 'edge' | 'source'
    alpha_before: float
    beta_before: float
    alpha_after: float
    beta_after: float

    @property
    def credibility_before(self) -> float:
        return self.alpha_before / (self.alpha_before + self.beta_before)

    @property
    def credibility_after(self) -> float:
        return self.alpha_after / (self.alpha_after + self.beta_after)


def compute_proportional_credits(edges: list[ContributingEdge]) -> dict[str, float]:
    """Map ``edge_id -> credit fraction`` proportional to ``influence_weight``.

    Credits sum to 1.0 across the edges. If the total influence weight is zero (degenerate upstream
    data) credit is split equally instead. Returns an empty dict for an empty edge list.
    """
    if not edges:
        return {}
    total = sum(edge.influence_weight for edge in edges)
    if total <= 0.0:
        equal = 1.0 / len(edges)
        return {edge.edge_id: equal for edge in edges}
    return {edge.edge_id: edge.influence_weight / total for edge in edges}


def compute_source_credits(sources: list[str]) -> dict[str, float]:
    """Map ``source_domain -> credit fraction`` with equal weighting (``1 / n``).

    Domains are normalised to lowercase (defensive; Ingestion should already normalise). Credits
    sum to 1.0. Returns an empty dict for an empty source list.
    """
    if not sources:
        return {}
    normalised = [source.strip().lower() for source in sources]
    credit = 1.0 / len(normalised)
    # A domain repeated in one message earns credit once (its last occurrence); credit stays 1/n.
    return {domain: credit for domain in normalised}


def apply_bernoulli(
    alpha: float, beta: float, credit: float, *, is_correct: bool, floor: float
) -> tuple[float, float]:
    """Apply one Beta-Bernoulli observation and return the floored ``(alpha, beta)``.

    A hit adds ``credit`` to ``alpha``; a miss adds it to ``beta``. Both counts are floored at the
    uninformed prior so a value seeded below the floor is corrected upward on first touch.
    """
    if is_correct:
        alpha += credit
    else:
        beta += credit
    return max(alpha, floor), max(beta, floor)
