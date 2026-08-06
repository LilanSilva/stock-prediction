"""Value objects used by the notification engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Headline:
    title: str
    source_id: str


@dataclass(frozen=True)
class NotificationMessage:
    """Neutral representation of a prediction alert, passed to every channel."""

    company_name: str
    exchange: str
    ticker: str
    direction: str
    signal_strength: str
    confidence: float
    decided_at: datetime
    headlines: list[Headline] = field(default_factory=list)
