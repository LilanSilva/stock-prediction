"""Internal value objects for evaluation tracking and scoring."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from shared.schemas.messages import AssetId, ContributingEdge, Direction, Magnitude, PropagationHop


class EvaluationStatus(StrEnum):
    """Lifecycle of a prediction evaluation."""

    PENDING = "PENDING"
    AWAITING_PRICE = "AWAITING_PRICE"
    SCORED = "SCORED"
    WITHDRAWN = "WITHDRAWN"


@dataclass(frozen=True)
class EvaluationRecord:
    """A persisted evaluation of one immutable prediction."""

    prediction_id: uuid.UUID
    context_id: uuid.UUID
    asset_id: AssetId
    predicted_direction: Direction
    predicted_magnitude: Magnitude
    confidence: float
    decision_at: datetime
    baseline_session: date
    settlement_session: date
    market_calendar: str
    registry_version: str
    request_id: uuid.UUID
    correlation_id: uuid.UUID
    contributing_edges: list[ContributingEdge] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    propagation_chain: list[PropagationHop] = field(default_factory=list)
    status: EvaluationStatus = EvaluationStatus.PENDING


@dataclass(frozen=True)
class ScoreOutcome:
    """The deterministic close-to-close score for one evaluation."""

    actual_return: float
    actual_direction: Direction
    actual_magnitude: Magnitude
    is_correct: bool
    score: float
