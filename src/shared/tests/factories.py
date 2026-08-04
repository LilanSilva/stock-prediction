"""Factory helpers producing valid canonical messages for tests."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from shared.reference import REGISTRY_VERSION
from shared.schemas.messages import (
    ArticleIngested,
    CloseObservation,
    ContributingEdge,
    DecisionMethod,
    Direction,
    EventDetected,
    EventType,
    ExtractionMethod,
    Horizon,
    Magnitude,
    PredictionMade,
    PredictionScored,
    PriceKind,
    PriceObserved,
    PriceRequested,
    SourceRef,
)

_NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _corr() -> uuid.UUID:
    return uuid.uuid4()


def make_article() -> ArticleIngested:
    return ArticleIngested(
        correlation_id=_corr(),
        occurred_at=_NOW,
        article_id=uuid.uuid4(),
        source_id="di",
        canonical_url="https://example.se/a",
        title="Title",
        body="Body text",
        published_at=_NOW,
        language="sv",
        country="SE",
        content_hash="abc123",
    )


def make_event() -> EventDetected:
    return EventDetected(
        correlation_id=_corr(),
        occurred_at=_NOW,
        event_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        canonical_summary="Conflict escalates",
        event_type=EventType.MILITARY_CONFLICT,
        actor="StateA",
        action="attack",
        object="StateB",
        entities=["StateA", "StateB"],
        affected_asset_ids=["GOLD", "BRENT_OIL"],
        first_seen_at=_NOW,
        last_seen_at=_NOW,
        sources=[
            SourceRef(
                article_id=uuid.uuid4(),
                source_id="di",
                canonical_url="https://example.se/a",
                title="Title",
                published_at=_NOW,
            )
        ],
        fact_conflicts=[],
        extraction_method=ExtractionMethod.LOCAL,
        llm_metadata=None,
    )


def _edge() -> ContributingEdge:
    return ContributingEdge(
        edge_id="MILITARY_CONFLICT->GOLD",
        direction=Direction.UP,
        current_weight=0.75,
        influence_weight=0.6,
        path="MILITARY_CONFLICT-[CAUSES]->GOLD",
    )


def make_prediction() -> PredictionMade:
    return PredictionMade(
        correlation_id=_corr(),
        occurred_at=_NOW,
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        context_version=1,
        event_ids=[uuid.uuid4()],
        asset_id="GOLD",
        direction=Direction.UP,
        magnitude=Magnitude.MEDIUM,
        confidence=0.7,
        horizon=Horizon.ONE_TRADING_DAY,
        rationale="Graph forces favor UP",
        contributing_edges=[_edge()],
        decision_at=_NOW,
        supersedes_prediction_id=None,
        decision_method=DecisionMethod.GRAPH_ONLY,
        llm_metadata=None,
    )


def make_price_requested() -> PriceRequested:
    return PriceRequested(
        correlation_id=_corr(),
        occurred_at=_NOW,
        request_id=uuid.uuid4(),
        prediction_id=uuid.uuid4(),
        asset_id="GOLD",
        baseline_session=date(2026, 7, 13),
        settlement_session=date(2026, 7, 14),
        market_calendar="America/New_York",
    )


def _close(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        provider_bar_time=_NOW,
        fetched_at=_NOW,
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version=REGISTRY_VERSION,
    )


def make_price_observed() -> PriceObserved:
    return PriceObserved(
        correlation_id=_corr(),
        occurred_at=_NOW,
        request_id=uuid.uuid4(),
        prediction_id=uuid.uuid4(),
        asset_id="GOLD",
        baseline=_close(date(2026, 7, 13), "2400.5"),
        settlement=_close(date(2026, 7, 14), "2430.0"),
    )


def make_prediction_scored() -> PredictionScored:
    return PredictionScored(
        correlation_id=_corr(),
        occurred_at=_NOW,
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        asset_id="GOLD",
        predicted_direction=Direction.UP,
        actual_direction=Direction.UP,
        predicted_magnitude=Magnitude.MEDIUM,
        actual_magnitude=Magnitude.MEDIUM,
        confidence=0.7,
        actual_return=0.0123,
        is_correct=True,
        score=1.0,
        contributing_edges=[_edge()],
        source_ids=["di"],
        baseline=_close(date(2026, 7, 13), "2400.5"),
        settlement=_close(date(2026, 7, 14), "2430.0"),
        scored_at=_NOW,
    )


ALL_FACTORIES = [
    make_article,
    make_event,
    make_prediction,
    make_price_requested,
    make_price_observed,
    make_prediction_scored,
]
