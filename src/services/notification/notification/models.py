"""Value objects used by the notification engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NotificationMessage:
    """Neutral representation of a prediction alert, passed to every channel."""

    company_name: str
    exchange: str       # e.g. "NYSE"
    ticker: str         # e.g. "LMT"
    direction: str      # "UP" | "DOWN" | "NEUTRAL"
    signal_strength: str  # "HIGH" | "MEDIUM" | "LOW"
    confidence: float   # 0.0 – 1.0
    decided_at: datetime
