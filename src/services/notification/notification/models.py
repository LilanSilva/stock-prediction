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


@dataclass(frozen=True)
class VerificationMessage:
    """Neutral representation of a verification result alert, passed to every channel."""

    company_name: str
    exchange: str
    ticker: str
    predicted_direction: str
    actual_direction: str
    predicted_magnitude: str
    actual_magnitude: str
    actual_return: float
    confidence: float
    is_correct: bool
    scored_at: datetime
