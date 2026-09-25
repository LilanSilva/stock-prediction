"""Exchange-local sessions with UTC slots; no prices reconstructed for missed slots."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Session(BaseModel):
    model_config = ConfigDict(frozen=True)
    session: date
    opens_at: datetime
    closes_at: datetime
    next_open_at: datetime


class Slot(BaseModel):
    scheduled_at: datetime
    kind: Literal["REGULAR", "CLOSE_CHECK"]


def session_for(now: datetime, calendar_id: str, timezone: str) -> Session:
    import exchange_calendars as calendars  # type: ignore[import-untyped]
    from exchange_calendars.errors import CalendarError  # type: ignore[import-untyped]

    try:
        cal: Any = calendars.get_calendar(calendar_id)
    except CalendarError as exc:
        raise ValueError("unsupported exchange calendar") from exc
    if str(cal.tz) != timezone:
        raise ValueError("calendar timezone mismatch")
    day = cal.date_to_session(now.astimezone(cal.tz).date(), direction="next")
    if str(cal.session_break_start(day)) != "NaT":
        raise ValueError("split sessions unsupported")
    return Session(
        session=day.date(),
        opens_at=cal.session_open(day).to_pydatetime().astimezone(UTC),
        closes_at=cal.session_close(day).to_pydatetime().astimezone(UTC),
        next_open_at=cal.session_open(cal.next_session(day)).to_pydatetime().astimezone(UTC),
    )


def slots(window: Session) -> list[Slot]:
    times = []
    at = window.opens_at
    while at < window.closes_at:
        times.append(Slot(scheduled_at=at, kind="REGULAR"))
        at += timedelta(seconds=900)
    return times + [
        Slot(scheduled_at=window.closes_at + timedelta(seconds=delay), kind="CLOSE_CHECK")
        for delay in (0, 120, 300)
    ]
