import pytest

from ingestion.exceptions import AdapterError
from ingestion.models import RawArticle
from ingestion.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ResilientAdapter,
    wrap_with_breakers,
)


def _state(cb: CircuitBreaker) -> CircuitState:
    # Read the current state freshly so type-narrowing does not carry across mutating calls.
    return cb.state


def test_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=60)
    assert _state(cb) is CircuitState.CLOSED
    for _ in range(3):
        cb.record_failure()
    assert _state(cb) is CircuitState.OPEN
    assert cb.allows_request() is False


def test_success_resets_failures() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    # Only one failure since the reset, so still closed.
    assert cb.state is CircuitState.CLOSED


def test_half_open_after_cooldown_then_closes_on_success() -> None:
    cb = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.0)
    cb.record_failure()
    assert _state(cb) is CircuitState.OPEN
    # reset_timeout of 0 -> immediately eligible for a half-open probe.
    assert cb.allows_request() is True
    assert _state(cb) is CircuitState.HALF_OPEN
    cb.record_success()
    assert _state(cb) is CircuitState.CLOSED


def test_half_open_failure_reopens() -> None:
    cb = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.0)
    cb.record_failure()
    cb.allows_request()  # -> HALF_OPEN
    cb.record_failure()  # probe fails
    assert cb.state is CircuitState.OPEN


class _FakeAdapter:
    def __init__(self, source_id: str, *, fail: bool) -> None:
        self._source_id = source_id
        self._fail = fail
        self.calls = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    async def fetch(self) -> list[RawArticle]:
        self.calls += 1
        if self._fail:
            raise AdapterError("boom")
        return []


async def test_resilient_adapter_opens_and_fails_fast() -> None:
    fake = _FakeAdapter("di", fail=True)
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=60)
    adapter = ResilientAdapter(fake, breaker)

    # Two failures trip the breaker.
    for _ in range(2):
        with pytest.raises(AdapterError):
            await adapter.fetch()
    assert _state(breaker) is CircuitState.OPEN

    # Now the call is rejected without hitting the underlying adapter.
    with pytest.raises(CircuitOpenError):
        await adapter.fetch()
    assert fake.calls == 2  # the third call never reached the adapter


async def test_resilient_adapter_passes_through_on_success() -> None:
    fake = _FakeAdapter("svd", fail=False)
    adapter = ResilientAdapter(fake, CircuitBreaker())
    assert await adapter.fetch() == []
    assert adapter.source_id == "svd"


def test_wrap_with_breakers_gives_each_source_its_own_breaker() -> None:
    a = _FakeAdapter("di", fail=False)
    b = _FakeAdapter("dn", fail=False)
    wrapped = wrap_with_breakers([a, b], failure_threshold=1, reset_timeout_seconds=5)
    assert [w.source_id for w in wrapped] == ["di", "dn"]
    assert wrapped[0]._breaker is not wrapped[1]._breaker
