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

from market_data.adapters.router import PriceAdapter
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
        adapter: PriceAdapter,
        *,
        retry_backoff_base_seconds: float = 2.0,
        retry_backoff_max_seconds: float = 8.0,
        abandon_after_settlement_days: int = 7,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._backoff_base = retry_backoff_base_seconds
        self._backoff_max = retry_backoff_max_seconds
        self._abandon_after_days = abandon_after_settlement_days

    async def process(
        self, request: PendingRequest, *, now: datetime | None = None
    ) -> ProcessOutcome:
        """Advance a single request as far as currently possible."""
        current_time = now or datetime.now(UTC)
        series = resolve(request.asset_id)

        try:
            baseline = await self._observe_baseline(request)
        except PriceNotYetAvailableError as exc:
            abandoned = await self._defer_or_abandon(
                request, f"baseline pending: {exc}", current_time
            )
            return ProcessOutcome(
                completed=False,
                published=False,
                pending_reason="abandoned" if abandoned else "baseline",
            )

        # Completion is judged on the asset's own market clock: Stockholm closes hours before
        # New York, so waiting for 17:00 ET would delay every European close.
        if not is_session_complete(
            request.settlement_session,
            series.timezone,
            now=current_time,
            hour=series.session_completion_hour,
            minute=series.session_completion_minute,
        ):
            await self._defer(request, "settlement session not yet complete")
            return ProcessOutcome(
                completed=False, published=False, pending_reason="settlement_not_complete"
            )

        try:
            settlement = await self._adapter.get_close(request.asset_id, request.settlement_session)
        except PriceNotYetAvailableError as exc:
            abandoned = await self._defer_or_abandon(
                request, f"settlement pending: {exc}", current_time
            )
            return ProcessOutcome(
                completed=False,
                published=False,
                pending_reason="abandoned" if abandoned else "settlement_lag",
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

    async def _defer_or_abandon(
        self, request: PendingRequest, reason: str, now: datetime
    ) -> bool:
        """Defer a missing bar, or abandon it once the provider has had long enough to publish.

        The attempt is always made first, so a provider switch or a backfill can still recover an
        old request; only a failed attempt past the grace window is terminal.
        """
        grace_ends = request.settlement_session + timedelta(days=self._abandon_after_days)
        if now.date() <= grace_ends:
            await self._defer(request, reason)
            return False

        terminal_reason = (
            f"abandoned after {self._abandon_after_days} days past settlement "
            f"{request.settlement_session.isoformat()}: {reason}"
        )
        await self._repository.abandon_request(request.request_id, terminal_reason)
        logger.warning(
            "price_request_abandoned",
            request_id=str(request.request_id),
            asset_id=str(request.asset_id),
            settlement_session=request.settlement_session.isoformat(),
            attempts=request.attempts,
            reason=reason,
        )
        return True

    def _backoff(self, attempts: int) -> timedelta:
        seconds = min(self._backoff_base * (2**attempts), self._backoff_max)
        return timedelta(seconds=seconds)
