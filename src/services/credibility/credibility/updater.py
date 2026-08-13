"""Pure credit math (no I/O), unit-tested in isolation.

Two credit-assignment rules, matching the frozen ``PredictionScored`` contract:

  - Edges carry an explicit ``influence_weight``; credit is proportional to each edge's share of the
    total influence in the message (``compute_proportional_credits``).
  - Sources are a plain list of domain strings; credit is split equally across them
    (``compute_source_credits``).

Two different quantities move, because the two entity types are consumed differently:

  - **Sources** keep Beta-Bernoulli (``apply_bernoulli``): a hit adds credit to ``alpha``, a miss to
    ``beta``, both floored at the uninformed prior.
  - **KG edges** move their ``weight`` instead (``apply_weight_delta``). Reliability
    (``alpha``/``beta``) cancels out of the decision's net/total ratio whenever a single edge
    fires — 92% of predictions — so counting outcomes there had no observable effect. ``weight``
    is what feeds magnitude, so it is the quantity worth learning.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.schemas.messages import ContributingEdge


@dataclass(frozen=True)
class WeightUpdate:
    """A single entity's transition, captured for persistence and history.

    Sources move ``alpha``/``beta``; KG edges move ``weight`` and leave the counts unchanged, so
    ``weight_before``/``weight_after`` are set only for ``entity_type == 'edge'``.
    """

    entity_id: str
    entity_type: str  # 'edge' | 'source'
    alpha_before: float
    beta_before: float
    alpha_after: float
    beta_after: float
    weight_before: float | None = None
    weight_after: float | None = None

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


def apply_weight_delta(
    weight: float,
    credit: float,
    *,
    is_correct: bool,
    step: float,
    floor: float,
    ceiling: float = 1.0,
) -> float:
    """Apply one outcome observation to a KG edge's ``weight`` and return the clamped result.

    A hit raises the weight, a miss lowers it, by ``step`` scaled by the edge's ``credit`` share of
    the prediction (so an edge that contributed a tenth of the decision earns a tenth of the move).

    Clamped to ``[floor, ceiling]``. The floor is deliberately above zero: an edge encodes an
    expert-asserted causal relationship, so sustained bad outcomes should make it negligible rather
    than delete it or let it change sign. ``weight`` carries magnitude only — direction is a
    separate edge property and is never touched here.
    """
    delta = step * credit
    weight = weight + delta if is_correct else weight - delta
    return min(max(weight, floor), ceiling)
