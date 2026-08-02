"""Credibility pipeline: apply one PredictionScored to Neo4j edges and Postgres source scores.

Ordering (the dual-store update is not a distributed transaction; full 2PC is out of scope for the
POC per T01's notes):

  1. Early-out if the ``prediction_id`` was already processed (common at-least-once redelivery).
  2. Read each contributing edge's current ``alpha``/``beta`` from Neo4j, apply proportional
     Beta-Bernoulli credit, and write the new counts back via the shared graph client. A missing
     edge is logged and skipped (the remaining edges still process).
  3. Read each source's current state from Postgres, apply equal credit.
  4. Commit the Postgres side atomically: the idempotency guard row, the source-state upserts, and
     one history row per entity (edges included). If this commit fails after the Neo4j writes
     succeeded, a CRITICAL is logged with the ``prediction_id`` for manual replay.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import structlog
from shared.graph.models import FiringEdge
from shared.schemas.messages import AssetId, ConditionCode, EventType, PredictionScored

from credibility.exceptions import InvalidScoredMessageError
from credibility.updater import (
    WeightUpdate,
    apply_bernoulli,
    compute_proportional_credits,
    compute_source_credits,
)

logger = structlog.get_logger(__name__)


class GraphClient(Protocol):
    """Structural type for the shared causal-graph client (keeps the pipeline unit-testable).

    Both methods already exist on ``shared.graph.CausalGraphClient``; the pipeline reads current
    counts via ``get_firing_edges`` and writes them back via ``update_edge_weight``.
    """

    async def get_firing_edges(
        self, event_type: EventType, asset_ids: list[AssetId] | None = None
    ) -> list[FiringEdge]: ...

    async def update_edge_weight(
        self,
        factor_id: EventType,
        asset_id: AssetId,
        *,
        alpha: float,
        beta: float,
        condition: ConditionCode | None = None,
    ) -> None: ...


class Repository(Protocol):
    """Structural type for the credibility repository."""

    async def already_processed(self, prediction_id: uuid.UUID) -> bool: ...

    async def get_source_state(self, source_id: str) -> tuple[float, float] | None: ...

    async def commit_updates(
        self, prediction_id: uuid.UUID, updates: list[WeightUpdate]
    ) -> bool: ...


def parse_edge_id(edge_id: str) -> tuple[EventType, ConditionCode | None, AssetId]:
    """Parse a contributing ``edge_id`` into its Neo4j lookup keys.

    Two forms of the deterministic business key produced by ``FiringEdge.edge_id`` are accepted:
    ``'FACTOR->ASSET'`` (unconditional/legacy, condition is ``None``) and
    ``'FACTOR|CONDITION->ASSET'`` (conditioned). A malformed or unknown value is terminal (the
    message is dead-lettered), never retried.
    """
    left, sep, asset_str = edge_id.partition("->")
    if not sep or not left or not asset_str:
        raise InvalidScoredMessageError(
            f"malformed edge_id {edge_id!r} (expected 'FACTOR->ASSET' or 'FACTOR|CONDITION->ASSET')"
        )
    factor_str, cond_sep, condition_str = left.partition("|")
    try:
        factor = EventType(factor_str)
        asset = AssetId(asset_str)
        condition = ConditionCode(condition_str) if cond_sep else None
    except ValueError as exc:
        raise InvalidScoredMessageError(
            f"unknown factor/condition/asset in edge_id {edge_id!r}"
        ) from exc
    return factor, condition, asset


class CredibilityPipeline:
    """Applies Beta-Bernoulli credit from scored predictions to edges (Neo4j) and sources (PG)."""

    def __init__(self, repository: Repository, graph: GraphClient, *, prior_floor: float) -> None:
        self._repo = repository
        self._graph = graph
        self._floor = prior_floor

    async def _update_edges(self, message: PredictionScored) -> list[WeightUpdate]:
        """Apply proportional credit to each contributing edge in Neo4j; return the transitions."""
        credits = compute_proportional_credits(list(message.contributing_edges))
        updates: list[WeightUpdate] = []
        for edge in message.contributing_edges:
            factor_id, condition, asset_id = parse_edge_id(edge.edge_id)
            firing = await self._graph.get_firing_edges(factor_id, [asset_id])
            # Match on the full business key so the right conditioned edge is picked when several
            # conditions share one (factor, asset) pair.
            current = next((e for e in firing if e.edge_id == edge.edge_id), None)
            if current is None:
                logger.warning(
                    "edge_missing_in_graph",
                    edge_id=edge.edge_id,
                    prediction_id=str(message.prediction_id),
                )
                continue
            alpha_before, beta_before = current.alpha, current.beta
            alpha_after, beta_after = apply_bernoulli(
                alpha_before,
                beta_before,
                credits[edge.edge_id],
                is_correct=message.is_correct,
                floor=self._floor,
            )
            await self._graph.update_edge_weight(
                factor_id, asset_id, alpha=alpha_after, beta=beta_after, condition=condition
            )
            updates.append(
                WeightUpdate(
                    entity_id=edge.edge_id,
                    entity_type="edge",
                    alpha_before=alpha_before,
                    beta_before=beta_before,
                    alpha_after=alpha_after,
                    beta_after=beta_after,
                )
            )
        return updates

    async def _update_sources(self, message: PredictionScored) -> list[WeightUpdate]:
        """Apply equal credit to each source score in Postgres; return the transitions."""
        credits = compute_source_credits(list(message.source_ids))
        if not credits:
            logger.warning(
                "scored_message_has_no_sources",
                prediction_id=str(message.prediction_id),
            )
            return []
        updates: list[WeightUpdate] = []
        for source_id, credit in credits.items():
            state = await self._repo.get_source_state(source_id)
            alpha_before, beta_before = state if state is not None else (self._floor, self._floor)
            alpha_after, beta_after = apply_bernoulli(
                alpha_before,
                beta_before,
                credit,
                is_correct=message.is_correct,
                floor=self._floor,
            )
            updates.append(
                WeightUpdate(
                    entity_id=source_id,
                    entity_type="source",
                    alpha_before=alpha_before,
                    beta_before=beta_before,
                    alpha_after=alpha_after,
                    beta_after=beta_after,
                )
            )
        return updates

    async def process(self, message: PredictionScored) -> bool:
        """Apply the scored prediction. Returns False when it was already processed (no-op)."""
        if await self._repo.already_processed(message.prediction_id):
            logger.info("scored_duplicate_skipped", prediction_id=str(message.prediction_id))
            return False

        edge_updates = await self._update_edges(message)
        source_updates = await self._update_sources(message)
        all_updates = edge_updates + source_updates

        try:
            committed = await self._repo.commit_updates(message.prediction_id, all_updates)
        except Exception:
            # Neo4j edge writes have already landed; the guard was not claimed, so a retry would
            # re-apply edge credit. Surface loudly for manual replay (documented MVP limitation).
            logger.critical(
                "credibility_postgres_commit_failed_after_graph_write",
                prediction_id=str(message.prediction_id),
                edges_updated=len(edge_updates),
            )
            raise

        if not committed:
            # Lost the race to a concurrent delivery; the winner applied everything.
            logger.info("scored_duplicate_skipped", prediction_id=str(message.prediction_id))
            return False

        logger.info(
            "prediction_credibility_applied",
            prediction_id=str(message.prediction_id),
            is_correct=message.is_correct,
            edges_updated=len(edge_updates),
            sources_updated=len(source_updates),
        )
        return True
