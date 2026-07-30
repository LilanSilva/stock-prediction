"""Tests for the dual-session request processor (pending vs. completed transitions).

Uses in-memory fakes for the repository and adapter so no live Postgres/provider is required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from shared.schemas.messages import AssetId, CloseObservation, PriceKind, PriceObserved

from market_data.exceptions import PriceNotYetAvailableError
from market_data.handler import PriceRequestProcessor
from market_data.storage import STATE_PENDING, PendingRequest

_BASELINE = date(2026, 7, 10)
_SETTLEMENT = date(2026, 7, 13)
_AFTER_SETTLEMENT = datetime(2026, 7, 13, 21, 0, tzinfo=UTC)
_BEFORE_SETTLEMENT = datetime(2026, 7, 11, 21, 0, tzinfo=UTC)


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


def _request() -> PendingRequest:
    return PendingRequest(
        request_id=uuid.uuid4(),
        prediction_id=uuid.uuid4(),
        asset_id=AssetId.GOLD,
        baseline_session=_BASELINE,
        settlement_session=_SETTLEMENT,
        market_calendar="COMEX",
        correlation_id=uuid.uuid4(),
        state=STATE_PENDING,
        attempts=0,
    )


class FakeAdapter:
    def __init__(self, available: dict[date, str]) -> None:
        self._available = available

    async def get_close(self, asset_id: AssetId, session: date) -> CloseObservation:
        if session not in self._available:
            raise PriceNotYetAvailableError(f"no bar for {session}")
        return _observation(session, self._available[session])


class FakeRepository:
    def __init__(self) -> None:
        self.completed: list[PriceObserved] = []
        self.baseline_marks: list[uuid.UUID] = []
        self.failures: list[tuple[uuid.UUID, str]] = []

    async def mark_baseline_observed(
        self, request_id: uuid.UUID, asset_id: AssetId, baseline: CloseObservation
    ) -> None:
        self.baseline_marks.append(request_id)

    async def complete_request(self, message: PriceObserved) -> bool:
        self.completed.append(message)
        return True

    async def record_failure(
        self, request_id: uuid.UUID, error: str, *, next_attempt_at: datetime | None
    ) -> None:
        self.failures.append((request_id, error))


def _processor(repo: FakeRepository, adapter: FakeAdapter) -> PriceRequestProcessor:
    return PriceRequestProcessor(repo, adapter)  # type: ignore[arg-type]


async def test_completes_when_both_sessions_available() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0", _SETTLEMENT: "3290.25"})
    outcome = await _processor(repo, adapter).process(_request(), now=_AFTER_SETTLEMENT)

    assert outcome.completed is True
    assert outcome.published is True
    assert len(repo.completed) == 1
    published = repo.completed[0]
    assert published.baseline.close == Decimal("3315.0")
    assert published.settlement.close == Decimal("3290.25")


async def test_pending_when_settlement_session_not_complete() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0", _SETTLEMENT: "3290.25"})
    outcome = await _processor(repo, adapter).process(_request(), now=_BEFORE_SETTLEMENT)

    assert outcome.completed is False
    assert outcome.pending_reason == "settlement_not_complete"
    assert repo.completed == []
    assert repo.failures  # a bounded next attempt was recorded


async def test_pending_when_settlement_bar_not_yet_published() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0"})  # settlement bar missing despite session complete
    outcome = await _processor(repo, adapter).process(_request(), now=_AFTER_SETTLEMENT)

    assert outcome.completed is False
    assert outcome.pending_reason == "settlement_lag"
    assert repo.completed == []


async def test_pending_when_baseline_not_available() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({})
    outcome = await _processor(repo, adapter).process(_request(), now=_AFTER_SETTLEMENT)

    assert outcome.completed is False
    assert outcome.pending_reason == "baseline"
    assert repo.baseline_marks == []
