"""Dual-session price-request processing.

`PriceRequestProcessor` drives one request through its lifecycle without look-ahead:

  1. Fetch the baseline-session close (baseline is a past session, normally already available).
  2. Once the settlement session is complete (17:00 local) and its bar is published, fetch it too.
  3. Persist both immutable closes and enqueue exactly one PriceObserved via the outbox.

Sessions that are not yet available leave the request PENDING/BASELINE_OBSERVED with a bounded next
attempt (the scheduler retries). Terminal provider-data errors are raised for the caller to
dead-letter. Correctness is never evaluated here and provider closes are never relabelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from shared.reference import resolve
from shared.schemas.messages import CloseObservation

from market_data.adapters.yahoo_chart import YahooChartAdapter
from market_data.exceptions import PriceNotYetAvailableError
from market_data.sessions import is_session_complete
from market_data.storage import (
    STATE_PENDING,
    PendingRequest,
    PriceRequestRepository,
    build_price_observed,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProcessOutcome:
    """Result of one processing attempt, for scheduling and observability."""

    completed: bool
    published: bool
    pending_reason: str | None = None


class PriceRequestProcessor:
    """Fetches reference closes and completes a price request through the outbox."""

    def __init__(
        self,
        repository: PriceRequestRepository,
        adapter: YahooChartAdapter,
        *,
        retry_backoff_base_seconds: float = 2.0,
        retry_backoff_max_seconds: float = 8.0,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._backoff_base = retry_backoff_base_seconds
        self._backoff_max = retry_backoff_max_seconds

    async def process(
        self, request: PendingRequest, *, now: datetime | None = None
    ) -> ProcessOutcome:
        """Advance a single request as far as currently possible."""
        current_time = now or datetime.now(UTC)
        series = resolve(request.asset_id)

        try:
            baseline = await self._observe_baseline(request)
        except PriceNotYetAvailableError as exc:
            await self._defer(request, f"baseline pending: {exc}")
            return ProcessOutcome(completed=False, published=False, pending_reason="baseline")

        if not is_session_complete(request.settlement_session, series.timezone, now=current_time):
            await self._defer(request, "settlement session not yet complete")
            return ProcessOutcome(
                completed=False, published=False, pending_reason="settlement_not_complete"
            )

        try:
            settlement = await self._adapter.get_close(request.asset_id, request.settlement_session)
        except PriceNotYetAvailableError as exc:
            await self._defer(request, f"settlement pending: {exc}")
            return ProcessOutcome(
                completed=False, published=False, pending_reason="settlement_lag"
            )

        message = build_price_observed(request, baseline, settlement)
        published = await self._repository.complete_request(message)
        logger.info(
            "price_request_completed",
            request_id=str(request.request_id),
            asset_id=str(request.asset_id),
            baseline_session=request.baseline_session.isoformat(),
            settlement_session=request.settlement_session.isoformat(),
            newly_published=published,
        )
        return ProcessOutcome(completed=True, published=published)

    async def _observe_baseline(self, request: PendingRequest) -> CloseObservation:
        baseline = await self._adapter.get_close(request.asset_id, request.baseline_session)
        if request.state == STATE_PENDING:
            await self._repository.mark_baseline_observed(
                request.request_id, request.asset_id, baseline
            )
        return baseline

    async def _defer(self, request: PendingRequest, reason: str) -> None:
        next_attempt = datetime.now(UTC) + self._backoff(request.attempts)
        await self._repository.record_failure(
            request.request_id, reason, next_attempt_at=next_attempt
        )
        logger.info(
            "price_request_deferred",
            request_id=str(request.request_id),
            reason=reason,
            next_attempt_at=next_attempt.isoformat(),
        )

    def _backoff(self, attempts: int) -> timedelta:
        seconds = min(self._backoff_base * (2**attempts), self._backoff_max)
        return timedelta(seconds=seconds)
