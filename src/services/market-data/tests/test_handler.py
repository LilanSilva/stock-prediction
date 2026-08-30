"""Tests for the dual-session request processor (pending vs. completed transitions).

Uses in-memory fakes for the repository and adapter so no live Postgres/provider is required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from shared.reference import REGISTRY_VERSION
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
        registry_version=REGISTRY_VERSION,
    )


def _request() -> PendingRequest:
    return PendingRequest(
        request_id=uuid.uuid4(),
        prediction_id=uuid.uuid4(),
        asset_id=AssetId.NEM_NYSE,
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
        self.abandoned: list[tuple[uuid.UUID, str]] = []

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

    async def abandon_request(self, request_id: uuid.UUID, reason: str) -> None:
        self.abandoned.append((request_id, reason))


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


async def test_abandons_when_bar_still_missing_long_after_settlement() -> None:
    # A bar the provider never published must terminate, not retry for the life of the deployment.
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0"})
    long_after = datetime(2026, 7, 25, 21, 0, tzinfo=UTC)  # settlement + 12 days

    outcome = await _processor(repo, adapter).process(_request(), now=long_after)

    assert outcome.completed is False
    assert outcome.pending_reason == "abandoned"
    assert len(repo.abandoned) == 1
    assert repo.completed == []
    assert repo.failures == []


async def test_does_not_abandon_inside_the_grace_window() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0"})
    within_window = datetime(2026, 7, 18, 21, 0, tzinfo=UTC)  # settlement + 5 days

    outcome = await _processor(repo, adapter).process(_request(), now=within_window)

    assert repo.abandoned == []
    assert outcome.pending_reason == "settlement_lag"


async def test_a_late_bar_still_completes_inside_the_grace_window() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0", _SETTLEMENT: "3290.25"})
    within_window = datetime(2026, 7, 19, 21, 0, tzinfo=UTC)  # settlement + 6 days

    outcome = await _processor(repo, adapter).process(_request(), now=within_window)

    assert outcome.completed is True
    assert repo.abandoned == []


async def test_recoverable_request_completes_even_long_past_the_grace_window() -> None:
    # The fetch is always attempted before abandoning, so a provider switch or backfill can still
    # rescue an old request instead of it being retired unread.
    repo = FakeRepository()
    adapter = FakeAdapter({_BASELINE: "3315.0", _SETTLEMENT: "3290.25"})
    long_after = datetime(2026, 8, 20, 21, 0, tzinfo=UTC)  # settlement + 38 days

    outcome = await _processor(repo, adapter).process(_request(), now=long_after)

    assert outcome.completed is True
    assert repo.abandoned == []


async def test_pending_when_baseline_not_available() -> None:
    repo = FakeRepository()
    adapter = FakeAdapter({})
    outcome = await _processor(repo, adapter).process(_request(), now=_AFTER_SETTLEMENT)

    assert outcome.completed is False
    assert outcome.pending_reason == "baseline"
    assert repo.baseline_marks == []
