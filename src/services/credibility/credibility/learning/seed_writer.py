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

from credibility.learning.models import EdgeEstimate

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
    ) -> None: ...


async def write_estimates(graph: EdgeUpserter, estimates: list[EdgeEstimate]) -> int:
    """Upsert each conditioned, non-NEUTRAL estimate; return the number of edges written."""
    written = 0
    for estimate in estimates:
        if estimate.direction is Direction.NEUTRAL or estimate.condition is None:
            continue
        await graph.upsert_conditioned_edge(
            estimate.factor,
            estimate.condition,
            estimate.asset,
            direction=estimate.direction,
            weight=estimate.weight,
            confidence=estimate.confidence,
            alpha=estimate.alpha,
            beta=estimate.beta,
        )
        written += 1
    logger.info("learning_edges_written", written=written, estimates=len(estimates))
    return written
