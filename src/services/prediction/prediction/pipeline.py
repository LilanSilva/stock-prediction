"""Prediction pipeline: event-time context aggregation and the graph-only close sweep."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

import structlog
from shared.graph import FiringEdge
from shared.graph.exceptions import GraphError
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    DecisionMethod,
    EventDetected,
    EventPolarity,
    EventType,
    Horizon,
    PredictionMade,
)

from prediction.config import PredictionSettings
from prediction.context import window_bounds
from prediction.decision import decide
from prediction.models import ContextEvent, ContextRecord, ContextState

logger = structlog.get_logger(__name__)


class PipelineRepository(Protocol):
    """Structural type for the repository (keeps the pipeline unit-testable)."""

    async def assign_event(
        self,
        *,
        asset_id: AssetId,
        event_id: uuid.UUID,
        event_type: EventType,
        first_seen_at: datetime,
        window_start: datetime,
        window_end: datetime,
        polarity: EventPolarity = EventPolarity.OCCURRENCE,
        context_tags: list[ConditionCode] | None = None,
    ) -> None: ...

    async def claim_ready_contexts(
        self, now: datetime, *, grace_minutes: int, limit: int = 20
    ) -> list[ContextRecord]: ...

    async def load_context_events(self, context_id: uuid.UUID) -> list[ContextEvent]: ...

    async def latest_prediction_id(
        self, asset_id: AssetId, window_start: datetime
    ) -> uuid.UUID | None: ...

    async def set_context_state(self, context_id: uuid.UUID, state: ContextState) -> None: ...

    async def store_prediction_with_outbox(
        self, message: PredictionMade, *, idempotency_key: str
    ) -> bool: ...


class GraphSource(Protocol):
    """Structural type for the causal-graph read client."""

    async def get_firing_edges(
        self,
        event_type: EventType,
        asset_ids: list[AssetId] | None = None,
        conditions: set[ConditionCode] | None = None,
    ) -> list[FiringEdge]: ...


class PriceSource(Protocol):
    """Structural type for the recent-price reader used by the Scope-B price gate."""

    async def is_elevated(self, asset_id: AssetId) -> bool: ...


class PredictionPipeline:
    """Aggregates events into contexts and closes ready contexts into graph-only predictions."""

    def __init__(
        self,
        repository: PipelineRepository,
        graph: GraphSource,
        price_reader: PriceSource,
        settings: PredictionSettings,
    ) -> None:
        self._repo = repository
        self._graph = graph
        self._price_reader = price_reader
        self._settings = settings

    async def process_event(self, event: EventDetected) -> None:
        """Add a distinct event to each affected asset's event-time context window."""
        if not event.affected_asset_ids:
            logger.info(
                "event_no_assets", event_id=str(event.event_id), event_type=event.event_type.value
            )
            return
        window_start, window_end = window_bounds(
            event.first_seen_at, self._settings.context_window_minutes
        )
        for asset_id in event.affected_asset_ids:
            await self._repo.assign_event(
                asset_id=asset_id,
                event_id=event.event_id,
                event_type=event.event_type,
                first_seen_at=event.first_seen_at,
                window_start=window_start,
                window_end=window_end,
                polarity=event.polarity,
                context_tags=list(event.context_tags),
            )
        logger.info(
            "event_contextualized",
            event_id=str(event.event_id),
            event_type=event.event_type.value,
            polarity=event.polarity.value,
            context_tags=[c.value for c in event.context_tags],
            assets=[a.value for a in event.affected_asset_ids],
        )

    async def _firing_edges(
        self, asset_id: AssetId, events: list[ContextEvent], *, elevated: bool
    ) -> list[FiringEdge]:
        # Distinct event types only: repeated coverage of one factor must not double-count it.
        # Each factor's conditioned edges are gated by that factor's aggregated context tags; an
        # empty tag set fires only the factor's unconditional edges. An elevated price injects the
        # asset-level RISK_PREMIUM_ELEVATED condition so any price-conditioned edges may fire.
        conditions_by_type = self._conditions_by_type(events)
        extra = {ConditionCode.RISK_PREMIUM_ELEVATED} if elevated else set()
        edges: list[FiringEdge] = []
        for event_type, conditions in conditions_by_type.items():
            edges.extend(
                await self._graph.get_firing_edges(
                    event_type, [asset_id], conditions=conditions | extra
                )
            )
        return edges

    @staticmethod
    def _conditions_by_type(
        events: list[ContextEvent],
    ) -> dict[EventType, set[ConditionCode]]:
        by_type: dict[EventType, set[ConditionCode]] = {}
        for event in events:
            by_type.setdefault(event.event_type, set()).update(event.context_tags)
        return by_type

    @staticmethod
    def _polarity_by_type(
        events: list[ContextEvent],
    ) -> dict[EventType, EventPolarity]:
        # Conservative: flip a factor only when every contributing event of that type resolved;
        # any OCCURRENCE (the default) leaves the edge's stored sign unchanged.
        polarities: dict[EventType, list[EventPolarity]] = {}
        for event in events:
            polarities.setdefault(event.event_type, []).append(event.polarity)
        return {
            event_type: EventPolarity.RESOLUTION
            if pols and all(p is EventPolarity.RESOLUTION for p in pols)
            else EventPolarity.OCCURRENCE
            for event_type, pols in polarities.items()
        }

    async def close_ready_contexts(self) -> int:
        """Close all due contexts into predictions. Returns the number of predictions produced."""
        now = datetime.now(UTC)
        claimed = await self._repo.claim_ready_contexts(
            now, grace_minutes=self._settings.close_grace_minutes
        )
        produced = 0
        for record in claimed:
            events = await self._repo.load_context_events(record.context_id)
            elevated = await self._price_reader.is_elevated(record.asset_id)
            try:
                edges = await self._firing_edges(record.asset_id, events, elevated=elevated)
            except GraphError as exc:
                logger.warning(
                    "graph_inference_deferred",
                    context_id=str(record.context_id),
                    error=str(exc),
                )
                await self._repo.set_context_state(
                    record.context_id, ContextState.ERROR_RETRYABLE
                )
                continue

            decision = decide(
                record.asset_id,
                edges,
                deadband=self._settings.decision_deadband,
                small_max=self._settings.magnitude_small_max,
                medium_max=self._settings.magnitude_medium_max,
                polarity_by_type=self._polarity_by_type(events),
                elevated=elevated,
            )
            if decision is None:
                await self._repo.set_context_state(record.context_id, ContextState.PREDICTED)
                logger.info(
                    "no_prediction",
                    context_id=str(record.context_id),
                    asset_id=record.asset_id.value,
                )
                continue

            supersedes = await self._repo.latest_prediction_id(
                record.asset_id, record.window_start
            )
            message = PredictionMade(
                correlation_id=uuid.uuid4(),
                occurred_at=now,
                prediction_id=uuid.uuid4(),
                context_id=record.context_id,
                context_version=record.context_version,
                event_ids=[e.event_id for e in events],
                asset_id=record.asset_id,
                direction=decision.direction,
                magnitude=decision.magnitude,
                confidence=decision.confidence,
                horizon=Horizon.ONE_TRADING_DAY,
                rationale=decision.rationale,
                contributing_edges=decision.contributing_edges,
                decision_at=now,
                supersedes_prediction_id=supersedes,
                decision_method=DecisionMethod.GRAPH_ONLY,
                llm_metadata=None,
            )
            key = (
                f"{record.asset_id.value}|{record.window_start.isoformat()}"
                f"|{Horizon.ONE_TRADING_DAY.value}|{record.context_version}"
            )
            if await self._repo.store_prediction_with_outbox(message, idempotency_key=key):
                produced += 1
                logger.info(
                    "prediction_made",
                    prediction_id=str(message.prediction_id),
                    asset_id=record.asset_id.value,
                    direction=decision.direction.value,
                    magnitude=decision.magnitude.value,
                    confidence=decision.confidence,
                    context_version=record.context_version,
                    edges=len(decision.contributing_edges),
                )
        return produced
