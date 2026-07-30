"""Unit tests for storage helpers that need no live database."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from shared.schemas.messages import AssetId, CloseObservation, PriceKind

from market_data.storage import (
    STATE_PENDING,
    PendingRequest,
    build_price_observed,
    observation_content_hash,
)


def _observation(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        provider_bar_time=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        fetched_at=datetime.now(UTC),
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version="biquote-reference-v1",
    )


def test_content_hash_is_stable_and_distinct() -> None:
    a = _observation(date(2026, 7, 13), "3290.25")
    b = _observation(date(2026, 7, 13), "3290.25")
    c = _observation(date(2026, 7, 13), "3291.00")
    assert observation_content_hash(AssetId.GOLD, a) == observation_content_hash(AssetId.GOLD, b)
    assert observation_content_hash(AssetId.GOLD, a) != observation_content_hash(AssetId.GOLD, c)


def test_build_price_observed_carries_both_closes_and_ids() -> None:
    request = PendingRequest(
        request_id=uuid.uuid4(),
        prediction_id=uuid.uuid4(),
        asset_id=AssetId.GOLD,
        baseline_session=date(2026, 7, 10),
        settlement_session=date(2026, 7, 13),
        market_calendar="COMEX",
        correlation_id=uuid.uuid4(),
        state=STATE_PENDING,
        attempts=0,
    )
    baseline = _observation(date(2026, 7, 10), "3315.0")
    settlement = _observation(date(2026, 7, 13), "3290.25")

    message = build_price_observed(request, baseline, settlement)

    assert message.request_id == request.request_id
    assert message.prediction_id == request.prediction_id
    assert message.correlation_id == request.correlation_id
    assert message.causation_id == request.request_id
    assert message.asset_id == AssetId.GOLD
    assert message.baseline.close == Decimal("3315.0")
    assert message.settlement.close == Decimal("3290.25")
