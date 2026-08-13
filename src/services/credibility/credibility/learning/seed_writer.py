"""Write learned edge estimates back to the causal graph.

Only conditioned, non-NEUTRAL estimates are written: NEUTRAL groups carry no directional signal,
and unconditional (``condition=None``) edges stay owned by the online Credibility consumer's
Beta-Bernoulli updates, not this offline learner. The upsert is idempotent, so re-running the
batch refines the same edges rather than duplicating them.
"""

from __future__ import annotations

from typing import Protocol

import structlog
from shared.schemas.messages import AssetId, ConditionCode, Direction, EventType

from credibility.learning.models import CorrelationEdgeEstimate, EdgeEstimate

logger = structlog.get_logger(__name__)


class EdgeUpserter(Protocol):
    """The subset of ``shared.graph.CausalGraphClient`` the writer depends on."""

    async def upsert_conditioned_edge(
        self,
        factor_id: EventType,
        condition: ConditionCode,
        asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> bool: ...


async def write_estimates(graph: EdgeUpserter, estimates: list[EdgeEstimate]) -> int:
    """Create each conditioned, non-NEUTRAL estimate; return the number of edges actually created.

    ``created`` and ``attempted`` are logged separately because the learner is add-only: an estimate
    for an edge that already exists is a deliberate no-op, and counting it as "written" would report
    graph changes that never happened.
    """
    created = 0
    attempted = 0
    for estimate in estimates:
        if estimate.direction is Direction.NEUTRAL or estimate.condition is None:
            continue
        attempted += 1
        if await graph.upsert_conditioned_edge(
            estimate.factor,
            estimate.condition,
            estimate.asset,
            direction=estimate.direction,
            weight=estimate.weight,
            confidence=estimate.confidence,
            alpha=estimate.alpha,
            beta=estimate.beta,
        ):
            created += 1
    logger.info(
        "learning_edges_written",
        created=created,
        attempted=attempted,
        existing_left_untouched=attempted - created,
        estimates=len(estimates),
    )
    return created


class CorrelationEdgeUpserter(Protocol):
    """The subset of CausalGraphClient the correlation writer depends on."""

    async def upsert_correlation_edge(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
        target_asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> bool: ...


async def write_correlation_estimates(
    graph: CorrelationEdgeUpserter,
    estimates: list[CorrelationEdgeEstimate],
) -> int:
    """Create each non-NEUTRAL correlation estimate; return the number actually created.

    Add-only, and reported the same way as ``write_estimates``.
    """
    created = 0
    attempted = 0
    for estimate in estimates:
        if estimate.direction is Direction.NEUTRAL:
            continue
        attempted += 1
        if await graph.upsert_correlation_edge(
            estimate.source_asset,
            estimate.condition,
            estimate.target_asset,
            direction=estimate.direction,
            weight=estimate.weight,
            confidence=estimate.confidence,
            alpha=estimate.alpha,
            beta=estimate.beta,
        ):
            created += 1
    logger.info(
        "corr_learning_edges_written",
        created=created,
        attempted=attempted,
        existing_left_untouched=attempted - created,
        estimates=len(estimates),
    )
    return created
