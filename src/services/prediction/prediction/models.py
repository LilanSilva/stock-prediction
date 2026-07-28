"""Internal value objects for context aggregation and the graph-only decision."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from shared.schemas.messages import (
    AssetId,
    ContributingEdge,
    Direction,
    EventType,
    Magnitude,
)


class ContextState(StrEnum):
    """Lifecycle of a per-asset prediction context version."""

    OPEN = "OPEN"
    READY = "READY"
    PREDICTING = "PREDICTING"
    PREDICTED = "PREDICTED"
    ERROR_RETRYABLE = "ERROR_RETRYABLE"


@dataclass(frozen=True)
class ContextRecord:
    """A claimed context version ready for graph inference."""

    context_id: uuid.UUID
    asset_id: AssetId
    context_version: int
    window_start: datetime
    window_end: datetime
    state: ContextState


@dataclass(frozen=True)
class ContextEvent:
    """One distinct event that is a member of a context."""

    event_id: uuid.UUID
    event_type: EventType
    first_seen_at: datetime


@dataclass(frozen=True)
class Decision:
    """The graph-only decision for one ready context."""

    direction: Direction
    magnitude: Magnitude
    confidence: float
    rationale: str
    contributing_edges: list[ContributingEdge] = field(default_factory=list)
