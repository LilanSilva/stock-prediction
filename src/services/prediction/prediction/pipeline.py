"""Prediction pipeline: event-time context aggregation and the graph-only close sweep."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Protocol

import structlog
from shared.calendar import is_trading_day, local_date_in
from shared.graph import CorrelationEdge, FiringEdge
from shared.graph.exceptions import GraphError
from shared.reference import UnknownAssetError, resolve
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    DecisionMethod,
    Direction,
    EventDetected,
    EventPolarity,
    EventType,
    Horizon,
    PredictionMade,
    PropagationHop,
)

from prediction.config import PredictionSettings
from prediction.context import window_bounds
from prediction.decision import decide
from prediction.models import ActivePrediction, ContextEvent, ContextRecord, ContextState, Decision
from prediction.research import ResearchRecorder

logger = structlog.get_logger(__name__)


class _ProxyRecord:
    """Re-targets a ContextRecord to a different asset_id for a propagated prediction.

    Propagated predictions belong to the source context but concern a downstream asset, so only
    ``asset_id`` differs; every other field is copied verbatim.
    """

    __slots__ = (
        "asset_id", "context_id", "context_version", "window_start",
        "window_end", "state",
    )

    def __init__(self, source: ContextRecord, asset_id: AssetId) -> None:
        self.asset_id = asset_id
        self.context_id = source.context_id
        self.context_version = source.context_version
        self.window_start = source.window_start
        self.window_end = source.window_end
        self.state = source.state


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

    async def latest_active_prediction(
        self, asset_id: AssetId
    ) -> ActivePrediction | None: ...

    async def count_predictions_on(
        self, asset_id: AssetId, local_date: date, timezone_name: str
    ) -> int: ...

    async def set_context_state(self, context_id: uuid.UUID, state: ContextState) -> None: ...

    async def store_prediction_with_outbox(
        self, message: PredictionMade, *, idempotency_key: str, withdraw_superseded: bool = False
    ) -> bool: ...


class GraphSource(Protocol):
    """Structural type for the causal-graph read client."""

    async def get_firing_edges(
        self,
        event_type: EventType,
        asset_ids: list[AssetId] | None = None,
        conditions: set[ConditionCode] | None = None,
    ) -> list[FiringEdge]: ...

    async def get_correlation_edges(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
    ) -> list[CorrelationEdge]: ...


class PriceSource(Protocol):
    """Structural type for the recent-price reader used by the Scope-B price gate."""

    async def is_elevated(self, asset_id: AssetId) -> bool: ...

    async def is_price_available(self, asset_id: AssetId) -> bool: ...


class PredictionPipeline:
    """Aggregates events into contexts and closes ready contexts into graph-only predictions."""

    def __init__(
        self,
        repository: PipelineRepository,
        graph: GraphSource,
        price_reader: PriceSource,
        settings: PredictionSettings,
        research: ResearchRecorder | None = None,
    ) -> None:
        self._repo = repository
        self._graph = graph
        self._price_reader = price_reader
        self._settings = settings
        self._research = research

    async def process_event(self, event: EventDetected) -> None:
        """Add a distinct event to each affected asset's event-time context window."""
        if self._research is not None:
            await self._research.record_event(event)
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

    async def _is_market_open(self, asset_id: AssetId, now: datetime) -> bool:
        # The market is "open" for stance purposes when today is a trading day on THAT asset's own
        # market calendar AND Market Data can currently serve a price. A non-trading day
        # (weekend/holiday) or an unreachable price feed selects the collapse-to-one path.
        try:
            timezone_name = resolve(asset_id).timezone
        except UnknownAssetError:
            # An unregistered asset cannot have a calendar; treat it as closed (the conservative
            # collapse-to-one path) rather than guessing a market.
            logger.warning("market_open_unknown_asset", asset_id=str(asset_id))
            return False
        if not is_trading_day(local_date_in(now, timezone_name)):
            return False
        return await self._price_reader.is_price_available(asset_id)

    async def _within_daily_limit(self, asset_id: AssetId, now: datetime) -> bool:
        """True when the asset may still take another stance today (its local trading day).

        Counted per asset in its own timezone, so a Stockholm and a New York listing each roll over
        at their own midnight. An unregistered asset has no calendar, so it is not limited here — it
        already takes the market-closed collapse path, which allows only one active stance anyway.
        """
        try:
            timezone_name = resolve(asset_id).timezone
        except UnknownAssetError:
            return True
        today = local_date_in(now, timezone_name)
        count = int(await self._repo.count_predictions_on(asset_id, today, timezone_name))
        return count < self._settings.max_daily_predictions_per_asset

    @staticmethod
    def _contributing_events(
        decision: Decision, events: list[ContextEvent]
    ) -> list[ContextEvent]:
        """The events whose factor fired an edge in this decision (E12 PRD-62).

        ``event_ids`` used to be every event in the asset's context window, so a prediction driven
        by
        one article cited every unrelated headline that happened to arrive in the same 15 minutes —
        up
        to five, spanning baseball, opinion polls and fantasy football on 2026-08-12. The same field
        feeds Notification's headline lookup, so users saw them too.

        Falls back to the full window when the filter would empty the list. That can only happen for
        a
        purely propagated decision (no factor) reached through this path, and a prediction with no
        recorded evidence is worse than one with imprecise evidence.
        """
        if not decision.contributing_factors:
            return events
        filtered = [e for e in events if e.event_type in decision.contributing_factors]
        return filtered or events

    async def _store_prediction(
        self,
        record: ContextRecord,
        decision: Decision,
        now: datetime,
        events: list[ContextEvent],
        *,
        propagation_depth: int = 0,
        propagation_chain: list[PropagationHop] | None = None,
    ) -> bool:
        """Persist one prediction (direct or propagated) via the outbox; True when stored."""
        active = await self._repo.latest_active_prediction(record.asset_id)
        market_open = await self._is_market_open(record.asset_id, now)

        # A propagated prediction never overturns a standing stance (E12 PRD-61). It carries no
        # causal factor — it is an inference from another asset's inference — so when it contradicts
        # what the asset already holds, the existing stance wins. Without this, one event reaching
        # both ends of an anti-correlated pair produced UP and DOWN on the same asset from the same
        # news: on 2026-08-12 NEM_NYSE held 16 UP and 13 DOWN, XOM_NYSE 16 DOWN and 13 UP.
        if (
            propagation_depth > 0
            and active is not None
            and active.direction != decision.direction
        ):
            logger.info(
                "propagation_contradicts_active_stance",
                context_id=str(record.context_id),
                asset_id=record.asset_id.value,
                active_direction=active.direction.value,
                propagated_direction=decision.direction.value,
                depth=propagation_depth,
            )
            return False

        if market_open:
            # A trading day allows at most `max_daily_predictions_per_asset` stances per asset,
            # and a further one only when the DIRECTION changes. Without this an asset accumulated
            # a new prediction on every arriving event — 52 in one day for one asset —
            # flip-flopping UP/DOWN and drowning the signal. A magnitude-only change no longer
            # emits: it is not a change of stance, only of degree.
            if active is not None and active.direction == decision.direction:
                logger.info(
                    "prediction_unchanged",
                    context_id=str(record.context_id),
                    asset_id=record.asset_id.value,
                    direction=decision.direction.value,
                )
                return False
            if not await self._within_daily_limit(record.asset_id, now):
                logger.info(
                    "prediction_daily_limit_reached",
                    context_id=str(record.context_id),
                    asset_id=record.asset_id.value,
                    direction=decision.direction.value,
                    limit=self._settings.max_daily_predictions_per_asset,
                )
                return False
            supersedes = None
            withdraw = False
        else:
            # Market closed: collapse to exactly one stance for the upcoming session by superseding
            # and withdrawing any prior one, so a weekend or holiday can never leave two open.
            supersedes = active.prediction_id if active is not None else None
            withdraw = supersedes is not None

        chain = propagation_chain or []
        message = PredictionMade(
            correlation_id=uuid.uuid4(),
            occurred_at=now,
            prediction_id=uuid.uuid4(),
            context_id=record.context_id,
            context_version=record.context_version,
            event_ids=[e.event_id for e in self._contributing_events(decision, events)],
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
            propagation_depth=propagation_depth,
            propagation_chain=chain,
        )
        key = (
            f"{record.asset_id.value}|{record.window_start.isoformat()}"
            f"|{Horizon.ONE_TRADING_DAY.value}|{record.context_version}"
            + (f"|prop{propagation_depth}" if propagation_depth > 0 else "")
        )
        stored = await self._repo.store_prediction_with_outbox(
            message, idempotency_key=key, withdraw_superseded=withdraw
        )
        if stored:
            logger.info(
                "prediction_made",
                prediction_id=str(message.prediction_id),
                asset_id=record.asset_id.value,
                direction=decision.direction.value,
                magnitude=decision.magnitude.value,
                confidence=decision.confidence,
                context_version=record.context_version,
                propagation_depth=propagation_depth,
                edges=len(decision.contributing_edges),
            )
        return stored

    async def _run_propagation(
        self,
        record: ContextRecord,
        # Mapping, not dict: dict is invariant in its value type, so a narrowed
        # dict[AssetId, Literal[UP, DOWN]] from the caller would not be accepted.
        direct_decisions: Mapping[AssetId, Direction],
        visited: set[AssetId],
        now: datetime,
        events: list[ContextEvent],
    ) -> int:
        """Propagate direct decisions through CORRELATES_WITH edges; return count stored.

        Each depth level is processed in two phases so that force summation works across
        converging edges (ADR-008):

          1. **Collect** — walk every source asset in the level and gather *all* correlation edges
             that reach each target, without deciding anything yet.
          2. **Decide** — for each target, call ``decide()`` once with the full edge list, so two
             upstream assets pushing on the same downstream asset combine (and can cancel below
             the deadband) instead of the first-reached edge winning outright.

        A target is added to ``visited`` only after its decision, so a second edge arriving in the
        *same* level can still contribute; cycle protection across levels is unchanged.
        """
        produced = 0
        current_pass: dict[AssetId, tuple[Direction, list[PropagationHop]]] = {
            asset: (direction, []) for asset, direction in direct_decisions.items()
        }
        depth = 0
        while current_pass and depth < self._settings.max_propagation_depth:
            depth += 1
            inbound = await self._collect_inbound(current_pass, visited, depth)

            next_pass: dict[AssetId, tuple[Direction, list[PropagationHop]]] = {}
            for target, arrivals in inbound.items():
                visited.add(target)
                decision = decide(
                    target,
                    [firing for firing, _, _ in arrivals],
                    deadband=self._settings.decision_deadband,
                    small_max=self._settings.magnitude_small_max,
                    medium_max=self._settings.magnitude_medium_max,
                    evidence_halfpoint=self._settings.confidence_evidence_halfpoint,
                )
                if decision is None or decision.direction is Direction.NEUTRAL:
                    logger.info(
                        "propagation_no_prediction",
                        target_asset=target.value,
                        depth=depth,
                        inbound_edges=len(arrivals),
                        reason="neutral_or_immaterial",
                    )
                    continue

                # Provenance: one hop per contributing edge, each recording the net decided
                # direction. The chain is seeded from the longest parent chain so depth still
                # reflects how far the signal travelled.
                parent_chain = max((chain for _, _, chain in arrivals), key=len)
                hops = [
                    PropagationHop(
                        source_asset_id=source,
                        target_asset_id=target,
                        condition=firing.condition or ConditionCode.UPSTREAM_UP,
                        direction=decision.direction,
                        edge_weight=firing.weight,
                    )
                    for firing, source, _ in arrivals
                ]
                chain = [*parent_chain, *hops]
                # Use a proxy ContextRecord with the propagated asset_id for _store_prediction.
                proxy = _ProxyRecord(record, target)
                stored = await self._store_prediction(
                    proxy,  # type: ignore[arg-type]
                    decision,
                    now,
                    events,
                    propagation_depth=depth,
                    propagation_chain=chain,
                )
                if stored:
                    produced += 1
                    next_pass[target] = (decision.direction, chain)
            current_pass = next_pass
        return produced

    async def _collect_inbound(
        self,
        current_pass: Mapping[AssetId, tuple[Direction, list[PropagationHop]]],
        visited: set[AssetId],
        depth: int,
    ) -> dict[AssetId, list[tuple[FiringEdge, AssetId, list[PropagationHop]]]]:
        """Gather every correlation edge reaching each unvisited target at this depth level.

        Returns ``target -> [(synthesised FiringEdge, source asset, that source's parent chain)]``.
        Deciding is deliberately deferred to the caller so all inbound forces are known first.
        """
        inbound: dict[AssetId, list[tuple[FiringEdge, AssetId, list[PropagationHop]]]] = {}
        for source_asset, (source_direction, parent_chain) in current_pass.items():
            condition = (
                ConditionCode.UPSTREAM_UP
                if source_direction is Direction.UP
                else ConditionCode.UPSTREAM_DOWN
            )
            try:
                corr_edges = await self._graph.get_correlation_edges(source_asset, condition)
            except GraphError as exc:
                logger.warning(
                    "propagation_graph_error",
                    source_asset=source_asset.value,
                    depth=depth,
                    error=str(exc),
                )
                continue
            for corr_edge in corr_edges:
                target = corr_edge.target_asset_id
                if target in visited:
                    continue
                # Synthesise a FiringEdge so decide() can be reused unchanged.
                firing = FiringEdge(
                    factor_id=None,  # propagated edge — no causal factor
                    asset_id=target,
                    direction=corr_edge.direction,
                    weight=corr_edge.weight,
                    confidence=corr_edge.confidence,
                    alpha=corr_edge.alpha,
                    beta=corr_edge.beta,
                    condition=condition,
                    correlation_source_id=source_asset,
                )
                inbound.setdefault(target, []).append((firing, source_asset, parent_chain))
        return inbound

    async def close_ready_contexts(self, now: datetime | None = None) -> int:
        """Close all due contexts into predictions. Returns the number of predictions produced.

        Two passes over the claimed batch (E12 PRD-61). Every DIRECT decision is made and stored
        first,
        then propagation runs. The order matters because sibling contexts arrive in the same batch:
        one
        macro event reaches both the gold and the oil proxy, each opens its own context, and each
        would
        propagate a contradiction onto the other. Deciding all the direct stances first means
        propagation can see them and stand down, which the per-context ``visited`` set cannot do —
        it is
        scoped to one context and is blind to its siblings.
        """
        now = now or datetime.now(UTC)
        claimed = await self._repo.claim_ready_contexts(
            now, grace_minutes=self._settings.close_grace_minutes
        )
        produced = 0
        # Pass 0 results, kept for the propagation pass: asset -> the direction decided directly.
        direct_by_asset: dict[AssetId, Direction] = {}
        deferred: list[tuple[ContextRecord, Decision, list[ContextEvent]]] = []
        for record in claimed:
            events = await self._repo.load_context_events(record.context_id)
            elevated = await self._price_reader.is_elevated(record.asset_id)
            try:
                edges = await self._firing_edges(record.asset_id, events, elevated=elevated)
            except GraphError as exc:
                if self._research is not None:
                    await self._research.capture(
                        record, events, [], elevated, None, datetime.now(UTC), graph_failed=True,
                    )
                logger.warning(
                    "graph_inference_deferred",
                    context_id=str(record.context_id),
                    error=str(exc),
                )
                await self._repo.set_context_state(
                    record.context_id, ContextState.ERROR_RETRYABLE
                )
                continue

            polarity_by_type = self._polarity_by_type(events)
            decision = decide(
                record.asset_id,
                edges,
                deadband=self._settings.decision_deadband,
                small_max=self._settings.magnitude_small_max,
                medium_max=self._settings.magnitude_medium_max,
                polarity_by_type=polarity_by_type,
                elevated=elevated,
                evidence_halfpoint=self._settings.confidence_evidence_halfpoint,
            )
            if self._research is not None:
                # Use actual input availability after reads, never the sweep's earlier `now`.
                # Capture before abstention, stance suppression, caps, and propagation.
                await self._research.capture(
                    record, events, edges, elevated, decision, datetime.now(UTC),
                )
            if decision is None:
                await self._repo.set_context_state(record.context_id, ContextState.PREDICTED)
                logger.info(
                    "no_prediction",
                    context_id=str(record.context_id),
                    asset_id=record.asset_id.value,
                )
                continue

            # Pass 0: direct prediction from CAUSES edges.
            stored = await self._store_prediction(record, decision, now, events)
            if stored:
                produced += 1
            if decision.direction in (Direction.UP, Direction.DOWN):
                direct_by_asset[record.asset_id] = decision.direction
                deferred.append((record, decision, events))
            else:
                await self._repo.set_context_state(record.context_id, ContextState.PREDICTED)

        # Passes 1+: propagate through CORRELATES_WITH edges, now that every direct stance is known.
        for record, decision, events in deferred:
            if decision.confidence < self._settings.propagation_min_confidence:
                # A propagated prediction is an inference from an inference, so a weak source is
                # amplified rather than diluted (E12 PRD-60).
                logger.info(
                    "propagation_skipped_low_confidence",
                    context_id=str(record.context_id),
                    asset_id=record.asset_id.value,
                    confidence=decision.confidence,
                    minimum=self._settings.propagation_min_confidence,
                )
            else:
                # Seed `visited` with the source asset AND every asset that already decided directly
                # in this batch: a direct decision has a causal factor behind it and outranks
                # anything propagation would infer for the same asset.
                visited: set[AssetId] = {record.asset_id} | set(direct_by_asset)
                produced += await self._run_propagation(
                    record, {record.asset_id: decision.direction}, visited, now, events
                )
            await self._repo.set_context_state(record.context_id, ContextState.PREDICTED)
        return produced
