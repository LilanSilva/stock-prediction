"""Versioned shadow policy: target hit and closing return answer different questions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from shared.schemas.messages import Direction, IntradayBar

MINUTE = timedelta(minutes=1)


class SessionWindow(BaseModel):
    model_config = ConfigDict(frozen=True)
    calendar_id: str
    opens_at: datetime
    closes_at: datetime


def resolve_window(decision_at: datetime, calendar_id: str, timezone: str) -> SessionWindow:
    """Use actual exchange sessions. Unsupported/break calendars fail closed for this policy."""
    import exchange_calendars as calendars  # type: ignore[import-untyped]

    if calendar_id not in calendars.get_calendar_names():
        raise ValueError("unknown exchange calendar")
    cal: Any = calendars.get_calendar(calendar_id)
    if str(cal.tz) != timezone:
        raise ValueError("exchange calendar timezone differs from the listing")
    session = cal.date_to_session(decision_at.astimezone(cal.tz).date(), direction="next")
    if decision_at >= cal.session_close(session).to_pydatetime():
        session = cal.next_session(session)
    # The initial policy supports continuous equity sessions only; a lunch break must not look
    # like missing data. Calendar packages return NaT for exchanges without a scheduled break.
    if str(cal.session_break_start(session)) != "NaT":
        raise ValueError("split sessions are not supported by intraday v1")
    return SessionWindow(
        calendar_id=calendar_id,
        opens_at=cal.session_open(session).to_pydatetime().astimezone(UTC),
        closes_at=cal.session_close(session).to_pydatetime().astimezone(UTC),
    )


class IntradayPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: Literal["INTRADAY_TARGET_V1"] = "INTRADAY_TARGET_V1"
    target_return: Decimal
    neutral_band: Decimal
    min_minutes: int
    max_baseline_delay_seconds: int


class IntradayResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["PENDING", "SCORED", "UNSCORABLE", "WITHDRAWN"] = "PENDING"
    reason: str | None = None
    baseline_at: datetime | None = None
    baseline_price: Decimal | None = None
    baseline_delay_seconds: float | None = None
    target_reached: bool | None = None
    first_hit_bar_at: datetime | None = None
    closing_return: Decimal | None = None
    closing_correct: bool | None = None
    max_up_return: Decimal | None = None
    max_down_return: Decimal | None = None
    expected_bars: int = 0
    observed_bars: int = 0
    complete: bool = False


def evaluate(
    *,
    decision_at: datetime,
    window: SessionWindow,
    direction: Direction,
    policy: IntradayPolicy,
    bars: list[IntradayBar],
    final: bool,
    previous: IntradayResult | None = None,
    failure: str | None = None,
) -> IntradayResult:
    start = max(decision_at, window.opens_at)
    first_minute = start.replace(second=0, microsecond=0)
    if first_minute < start:
        first_minute += MINUTE
    expected = int((window.closes_at - first_minute) / MINUTE)
    if expected < policy.min_minutes:
        return IntradayResult(status="UNSCORABLE", reason="INSUFFICIENT_WINDOW")
    eligible = sorted(
        (b for b in bars if first_minute <= b.start and b.start + MINUTE <= window.closes_at),
        key=lambda b: b.start,
    )
    if not eligible:
        return IntradayResult(
            status="UNSCORABLE" if final else "PENDING",
            reason=(failure or "NO_BASELINE") if final else None,
            expected_bars=expected,
        )
    baseline = eligible[0]
    delay = (baseline.start - start).total_seconds()
    if delay > policy.max_baseline_delay_seconds:
        return IntradayResult(
            status="UNSCORABLE" if final else "PENDING", reason="BASELINE_TOO_LATE"
        )
    if (
        previous
        and previous.baseline_at is not None
        and (previous.baseline_at != baseline.start or previous.baseline_price != baseline.open)
    ):
        return IntradayResult(
            status="UNSCORABLE",
            reason="BASELINE_REVISED",
            baseline_at=previous.baseline_at,
            baseline_price=previous.baseline_price,
            baseline_delay_seconds=previous.baseline_delay_seconds,
        )
    price = baseline.open
    up = max(b.high / price - 1 for b in eligible)
    down = min(b.low / price - 1 for b in eligible)
    hit: IntradayBar | None = None
    for bar in eligible:
        if (direction == Direction.UP and bar.high >= price * (1 + policy.target_return)) or (
            direction == Direction.DOWN and bar.low <= price * (1 - policy.target_return)
        ):
            hit = bar
            break
    stamps = {b.start for b in eligible}
    expected_stamps = {first_minute + i * MINUTE for i in range(expected)}
    complete = stamps == expected_stamps and not failure
    reached: bool | None = True if hit else (False if final and complete else None)
    if direction == Direction.NEUTRAL:
        breached = up > policy.neutral_band or down < -policy.neutral_band
        reached = False if breached else (True if final and complete else None)
    closing = eligible[-1].close / price - 1 if final and complete else None
    close_correct: bool | None = None
    if closing is not None:
        actual = (
            Direction.UP
            if closing > policy.neutral_band
            else Direction.DOWN
            if closing < -policy.neutral_band
            else Direction.NEUTRAL
        )
        close_correct = actual == direction
    return IntradayResult(
        status=("SCORED" if complete else "UNSCORABLE") if final else "PENDING",
        reason=(failure or "MISSING_BARS") if final and not complete else None,
        baseline_at=baseline.start,
        baseline_price=price,
        baseline_delay_seconds=delay,
        target_reached=reached,
        first_hit_bar_at=hit.start if hit else None,
        closing_return=closing,
        closing_correct=close_correct,
        max_up_return=up,
        max_down_return=down,
        expected_bars=expected,
        observed_bars=len(stamps),
        complete=complete,
    )
