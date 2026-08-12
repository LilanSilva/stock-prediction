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
from shared.reference.asset_registry import groups
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    EventType,
    PredictionScored,
)

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

    async def get_group_edge_counts(
        self,
        factor_id: EventType,
        group_id: str,
        condition: ConditionCode | None = None,
    ) -> tuple[float, float] | None: ...

    async def update_edge_weight(
        self,
        factor_id: EventType,
        asset_id: AssetId | str,
        *,
        alpha: float,
        beta: float,
        condition: ConditionCode | None = None,
        target_is_group: bool = False,
    ) -> None: ...

    async def get_correlation_edge_counts(
        self,
        source_asset_id: AssetId,
        target_asset_id: AssetId,
        condition: ConditionCode,
    ) -> tuple[float, float] | None: ...

    async def update_correlation_weight(
        self,
        source_asset_id: AssetId,
        target_asset_id: AssetId,
        condition: ConditionCode,
        *,
        alpha: float,
        beta: float,
    ) -> None: ...


class Repository(Protocol):
    """Structural type for the credibility repository."""

    async def already_processed(self, prediction_id: uuid.UUID) -> bool: ...

    async def get_source_state(self, source_id: str) -> tuple[float, float] | None: ...

    async def commit_updates(
        self, prediction_id: uuid.UUID, updates: list[WeightUpdate]
    ) -> bool: ...


def parse_correlation_edge_id(edge_id: str) -> tuple[AssetId, ConditionCode, AssetId] | None:
    """Parse a CORRELATES_WITH ``edge_id`` (``'SOURCE|UPSTREAM_*->TARGET'``), else ``None``.

    Returns ``None`` — rather than raising — when the id is not a correlation edge id, so the
    caller can fall through to :func:`parse_edge_id` for the ``CAUSES`` forms. Only the two
    ``UPSTREAM_*`` conditions identify a correlation edge; every other condition belongs to a
    ``CAUSES`` edge whose prefix is an ``EventType``.
    """
    left, sep, target_str = edge_id.partition("->")
    if not sep:
        return None
    source_str, cond_sep, condition_str = left.partition("|")
    if not cond_sep or condition_str not in (
        ConditionCode.UPSTREAM_UP.value,
        ConditionCode.UPSTREAM_DOWN.value,
    ):
        return None
    try:
        source = AssetId(source_str)
        target = AssetId(target_str)
    except ValueError as exc:
        raise InvalidScoredMessageError(
            f"correlation edge_id {edge_id!r} names an unknown asset"
        ) from exc
    return source, ConditionCode(condition_str), target


def parse_edge_id(edge_id: str) -> tuple[EventType, ConditionCode | None, AssetId | str]:
    """Parse a contributing ``edge_id`` into its Neo4j lookup keys.

    The deterministic business key produced by ``FiringEdge.edge_id`` is ``'FACTOR->TARGET'``
    (unconditional/legacy, condition is ``None``) or ``'FACTOR|CONDITION->TARGET'`` (conditioned).

    ``TARGET`` is either a registry-validated ``AssetId`` or an **industry group id**: a prediction
    that fired an inherited edge names the group edge, so learning lands on the shared prior that
    actually fired instead of a per-asset edge that was never seeded. An asset id is returned as
    ``AssetId``; a group id is returned as a plain ``str``, and callers distinguish the two by type.

    A malformed value, or a target that is neither a known asset nor a known group, is terminal (the
    message is dead-lettered), never retried.
    """
    left, sep, target_str = edge_id.partition("->")
    if not sep or not left or not target_str:
        raise InvalidScoredMessageError(
            f"malformed edge_id {edge_id!r} "
            "(expected 'FACTOR->TARGET' or 'FACTOR|CONDITION->TARGET')"
        )
    factor_str, cond_sep, condition_str = left.partition("|")
    try:
        factor = EventType(factor_str)
        condition = ConditionCode(condition_str) if cond_sep else None
    except ValueError as exc:
        raise InvalidScoredMessageError(
            f"unknown factor/condition in edge_id {edge_id!r}"
        ) from exc

    target: AssetId | str
    try:
        target = AssetId(target_str)
    except ValueError:
        # Not an asset: the only other legal target is an industry group (an inherited edge).
        if target_str not in groups():
            raise InvalidScoredMessageError(
                f"target {target_str!r} in edge_id {edge_id!r} is neither a known asset "
                "nor a known asset group"
            ) from None
        target = target_str
    return factor, condition, target


class CredibilityPipeline:
    """Applies Beta-Bernoulli credit from scored predictions to edges (Neo4j) and sources (PG)."""

    def __init__(self, repository: Repository, graph: GraphClient, *, prior_floor: float) -> None:
        self._repo = repository
        self._graph = graph
        self._floor = prior_floor

    async def _update_correlation_edge(
        self,
        source: AssetId,
        target: AssetId,
        condition: ConditionCode,
        credit: float,
        *,
        is_correct: bool,
    ) -> WeightUpdate | None:
        """Apply ``credit`` of Beta-Bernoulli evidence to one CORRELATES_WITH edge."""
        counts = await self._graph.get_correlation_edge_counts(source, target, condition)
        if counts is None:
            logger.warning(
                "correlation_edge_missing_in_graph",
                source_asset=source.value,
                target_asset=target.value,
                condition=condition.value,
            )
            return None
        alpha_before, beta_before = counts
        alpha_after, beta_after = apply_bernoulli(
            alpha_before, beta_before, credit, is_correct=is_correct, floor=self._floor
        )
        await self._graph.update_correlation_weight(
            source, target, condition, alpha=alpha_after, beta=beta_after
        )
        return WeightUpdate(
            entity_id=f"{source.value}|{condition.value}->{target.value}",
            entity_type="edge",
            alpha_before=alpha_before,
            beta_before=beta_before,
            alpha_after=alpha_after,
            beta_after=beta_after,
        )

    async def _update_correlation_edges(
        self, message: PredictionScored
    ) -> list[WeightUpdate]:
        """Credit every CORRELATES_WITH edge that contributed to this propagated prediction.

        Credit is proportional to each edge's influence, so when two upstream assets converged on
        one target neither is credited as though it acted alone. A single-edge propagation still
        receives the full 1.0, matching the previous behaviour.

        ``contributing_edges`` is the source of truth for *which* edges fired (it is what
        ``decide()`` reported). ``propagation_chain`` only marks the message as propagated and
        carries provenance/depth.
        """
        correlation_edges = [
            edge
            for edge in message.contributing_edges
            if parse_correlation_edge_id(edge.edge_id) is not None
        ]
        if not correlation_edges:
            # A propagated prediction whose contributing edges are all CAUSES edges should not
            # happen; log rather than silently crediting the wrong edge type.
            logger.warning(
                "propagated_prediction_without_correlation_edges",
                prediction_id=str(message.prediction_id),
                edge_ids=[e.edge_id for e in message.contributing_edges],
            )
            return []

        credits = compute_proportional_credits(correlation_edges)
        updates: list[WeightUpdate] = []
        for edge in correlation_edges:
            parsed = parse_correlation_edge_id(edge.edge_id)
            assert parsed is not None  # filtered above
            source, condition, target = parsed
            update = await self._update_correlation_edge(
                source,
                target,
                condition,
                credits[edge.edge_id],
                is_correct=message.is_correct,
            )
            if update is not None:
                updates.append(update)
        return updates

    async def _update_edges(self, message: PredictionScored) -> list[WeightUpdate]:
        """Apply proportional credit to each contributing edge in Neo4j; return the transitions."""
        # Propagated predictions: credit flows to the CORRELATES_WITH edge(s) that produced them,
        # not to the originating CAUSES edges (which belong to the direct/source prediction).
        # Where two upstream assets converged on this target, every contributing correlation edge
        # is credited in proportion to its influence — the same rule used for CAUSES edges.
        if message.propagation_chain:
            return await self._update_correlation_edges(message)

        credits = compute_proportional_credits(list(message.contributing_edges))
        updates: list[WeightUpdate] = []
        for edge in message.contributing_edges:
            factor_id, condition, target = parse_edge_id(edge.edge_id)
            counts: tuple[float, float] | None
            if isinstance(target, AssetId):
                firing = await self._graph.get_firing_edges(factor_id, [target])
                # Match on the full business key so the right conditioned edge is picked
                # when several conditions share one (factor, asset) pair.
                current = next((e for e in firing if e.edge_id == edge.edge_id), None)
                counts = (current.alpha, current.beta) if current is not None else None
            else:
                # A group target means the prediction fired an inherited industry edge. Read it
                # directly: get_firing_edges hides a group edge from members that own an edge
                # for the same pair, so a member-based lookup can miss the edge that fired.
                counts = await self._graph.get_group_edge_counts(factor_id, target, condition)
            target_is_group = not isinstance(target, AssetId)
            if counts is None:
                logger.warning(
                    "edge_missing_in_graph",
                    edge_id=edge.edge_id,
                    prediction_id=str(message.prediction_id),
                )
                continue
            alpha_before, beta_before = counts
            alpha_after, beta_after = apply_bernoulli(
                alpha_before,
                beta_before,
                credits[edge.edge_id],
                is_correct=message.is_correct,
                floor=self._floor,
            )
            await self._graph.update_edge_weight(
                factor_id,
                target,
                alpha=alpha_after,
                beta=beta_after,
                condition=condition,
                target_is_group=target_is_group,
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
