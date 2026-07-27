"""Per-source resilience: a circuit breaker plus a `ResilientAdapter` wrapper.

Each source gets its own `CircuitBreaker` instance so a flaky source cannot trip the breaker of a
healthy one. `ResilientAdapter` wraps any `SourceAdapter.fetch()` with the breaker: after repeated
failures the circuit opens and calls fail fast for a cooldown, then a single half-open probe decides
whether to close again. This is the mechanism the (optional) GDELT source will rely on for its
429/transport behavior; it applies uniformly to every source.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import structlog

from ingestion.exceptions import AdapterError
from ingestion.models import RawArticle
from ingestion.pipeline import SourceAdapter

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(AdapterError):
    """Raised when a call is rejected because the circuit is open."""


@dataclass
class CircuitBreaker:
    """A minimal failure-count circuit breaker with a time-based cooldown.

    - CLOSED: calls pass through. `failure_threshold` consecutive failures -> OPEN.
    - OPEN: calls are rejected until `reset_timeout_seconds` elapses -> HALF_OPEN.
    - HALF_OPEN: one probe is allowed; success -> CLOSED, failure -> OPEN again.
    """

    failure_threshold: int = 5
    reset_timeout_seconds: float = 60.0
    name: str = "circuit"
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    def _now(self) -> float:
        return time.monotonic()

    def allows_request(self) -> bool:
        """Return True if a call may proceed, transitioning OPEN -> HALF_OPEN when cooled down."""
        if self._state is CircuitState.OPEN:
            if self._now() - self._opened_at >= self.reset_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        if self._state is not CircuitState.CLOSED:
            logger.info("circuit_closed", circuit=self.name)
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            if self._state is not CircuitState.OPEN:
                logger.warning("circuit_opened", circuit=self.name, failures=self._failures)
            self._state = CircuitState.OPEN
            self._opened_at = self._now()


class ResilientAdapter:
    """Wrap a `SourceAdapter` so its `fetch()` is guarded by a per-source circuit breaker."""

    def __init__(self, adapter: SourceAdapter, breaker: CircuitBreaker) -> None:
        self._adapter = adapter
        self._breaker = breaker

    @property
    def source_id(self) -> str:
        return self._adapter.source_id

    async def fetch(self) -> list[RawArticle]:
        if not self._breaker.allows_request():
            raise CircuitOpenError(
                f"circuit open for source {self._adapter.source_id!r}; skipping fetch"
            )
        try:
            articles = await self._adapter.fetch()
        except AdapterError:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return articles


def wrap_with_breakers(
    adapters: Sequence[SourceAdapter],
    *,
    failure_threshold: int = 5,
    reset_timeout_seconds: float = 60.0,
) -> list[ResilientAdapter]:
    """Wrap each adapter with its own independent circuit breaker."""
    return [
        ResilientAdapter(
            adapter,
            CircuitBreaker(
                failure_threshold=failure_threshold,
                reset_timeout_seconds=reset_timeout_seconds,
                name=adapter.source_id,
            ),
        )
        for adapter in adapters
    ]
