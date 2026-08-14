"""Verification pipeline: evaluation creation (PriceRequested) and scoring (PredictionScored)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

import structlog
from shared.calendar import resolve_baseline_settlement
from shared.reference import resolve
from shared.reference.exceptions import UnknownAssetError
from shared.schemas.messages import (
    PredictionMade,
    PredictionScored,
    PriceObserved,
    PriceRequested,
)

from verification.config import VerificationSettings
from verification.exceptions import (
    InvalidPredictionError,
    OrphanedObservationError,
    PriceValidationError,
)
from verification.models import EvaluationRecord, EvaluationStatus
from verification.scoring import score

logger = structlog.get_logger(__name__)

# Stable namespace so request IDs are deterministic per (prediction, registry version).
_REQUEST_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "feed.verification.price-request")


class PipelineRepository(Protocol):
    """Structural type for the repository (keeps the pipeline unit-testable)."""

    async def create_evaluation_with_outbox(
        self, evaluation: EvaluationRecord, request: PriceRequested
    ) -> bool: ...

    async def store_price_observation(self, message: PriceObserved) -> bool: ...

    async def load_evaluation_by_request_id(
        self, request_id: uuid.UUID
    ) -> EvaluationRecord | None: ...

    async def store_score_with_outbox(self, scored: PredictionScored) -> bool: ...

    async def withdraw_evaluation(self, prediction_id: uuid.UUID) -> bool: ...


def _deterministic_request_id(prediction_id: uuid.UUID, registry_version: str) -> uuid.UUID:
    return uuid.uuid5(_REQUEST_NAMESPACE, f"{prediction_id}|{registry_version}")


class VerificationPipeline:
    """Creates evaluations from predictions and scores returned price observations."""

    def __init__(self, repository: PipelineRepository, settings: VerificationSettings) -> None:
        self._repo = repository
        self._settings = settings

    async def process_prediction(self, message: PredictionMade) -> None:
        """Resolve sessions and publish exactly one dual-session PriceRequested."""
        try:
            series = resolve(message.asset_id)
        except UnknownAssetError as exc:
            raise InvalidPredictionError(f"unknown asset {message.asset_id}: {exc}") from exc

        # Sessions resolve on the asset's own market calendar and closing clock: a Stockholm listing
        # settles against Stockholm sessions, not New York's.
        baseline_session, settlement_session = resolve_baseline_settlement(
            message.decision_at,
            series.timezone,
            hour=series.session_completion_hour,
            minute=series.session_completion_minute,
        )
        request_id = _deterministic_request_id(message.prediction_id, series.registry_version)
        now = datetime.now(UTC)

        request = PriceRequested(
            correlation_id=message.correlation_id,
            causation_id=message.message_id,
            occurred_at=now,
            request_id=request_id,
            prediction_id=message.prediction_id,
            asset_id=message.asset_id,
            baseline_session=baseline_session,
            settlement_session=settlement_session,
            market_calendar=series.timezone,
        )
        evaluation = EvaluationRecord(
            prediction_id=message.prediction_id,
            context_id=message.context_id,
            asset_id=message.asset_id,
            predicted_direction=message.direction,
            predicted_magnitude=message.magnitude,
            confidence=message.confidence,
            decision_at=message.decision_at,
            baseline_session=baseline_session,
            settlement_session=settlement_session,
            market_calendar=series.timezone,
            registry_version=series.registry_version,
            request_id=request_id,
            correlation_id=message.correlation_id,
            contributing_edges=list(message.contributing_edges),
            source_ids=[],
            propagation_chain=list(message.propagation_chain),
        )
        created = await self._repo.create_evaluation_with_outbox(evaluation, request)
        logger.info(
            "evaluation_created" if created else "evaluation_duplicate",
            prediction_id=str(message.prediction_id),
            asset_id=message.asset_id.value,
            baseline_session=baseline_session.isoformat(),
            settlement_session=settlement_session.isoformat(),
        )

        # A weekend/closed-market collapse supersedes the prior stance: withdraw its evaluation so
        # the replaced prediction is never scored (it never had its own price outcome).
        if message.supersedes_prediction_id is not None:
            withdrawn = await self._repo.withdraw_evaluation(message.supersedes_prediction_id)
            if withdrawn:
                logger.info(
                    "evaluation_withdrawn",
                    prediction_id=str(message.supersedes_prediction_id),
                    superseded_by=str(message.prediction_id),
                )

    def _validate_observation(
        self, message: PriceObserved, evaluation: EvaluationRecord
    ) -> None:
        series = resolve(evaluation.asset_id)
        if message.asset_id != evaluation.asset_id:
            raise PriceValidationError("asset mismatch")
        if message.baseline.session != evaluation.baseline_session:
            raise PriceValidationError("baseline session mismatch")
        if message.settlement.session != evaluation.settlement_session:
            raise PriceValidationError("settlement session mismatch")
        for observation in (message.baseline, message.settlement):
            if observation.registry_version != evaluation.registry_version:
                raise PriceValidationError("registry version mismatch")
            if observation.price_kind != series.price_kind:
                raise PriceValidationError("price kind mismatch")
            if observation.is_adjusted != series.is_adjusted:
                raise PriceValidationError("adjustment flag mismatch")

    async def process_price(self, message: PriceObserved) -> None:
        """Validate the dual-close observation and publish exactly one PredictionScored."""
        evaluation = await self._repo.load_evaluation_by_request_id(message.request_id)
        if evaluation is None:
            raise OrphanedObservationError(
                f"no evaluation for request {message.request_id} "
                f"(prediction {message.prediction_id}, asset {message.asset_id.value})"
            )
        if evaluation.status is EvaluationStatus.WITHDRAWN:
            # The prediction was superseded by a market-closed collapse; do not score it.
            logger.info(
                "score_skipped_withdrawn",
                prediction_id=str(evaluation.prediction_id),
                request_id=str(message.request_id),
            )
            return
        self._validate_observation(message, evaluation)

        await self._repo.store_price_observation(message)

        outcome = score(
            baseline_close=message.baseline.close,
            settlement_close=message.settlement.close,
            predicted_direction=evaluation.predicted_direction,
            deadband=self._settings.deadband,
            magnitude_medium_min=self._settings.magnitude_medium_min,
            magnitude_large_min=self._settings.magnitude_large_min,
        )
        now = datetime.now(UTC)
        scored = PredictionScored(
            correlation_id=evaluation.correlation_id,
            causation_id=message.message_id,
            occurred_at=now,
            prediction_id=evaluation.prediction_id,
            context_id=evaluation.context_id,
            asset_id=evaluation.asset_id,
            predicted_direction=evaluation.predicted_direction,
            actual_direction=outcome.actual_direction,
            predicted_magnitude=evaluation.predicted_magnitude,
            actual_magnitude=outcome.actual_magnitude,
            confidence=evaluation.confidence,
            actual_return=outcome.actual_return,
            is_correct=outcome.is_correct,
            score=outcome.score,
            contributing_edges=evaluation.contributing_edges,
            source_ids=evaluation.source_ids,
            baseline=message.baseline,
            settlement=message.settlement,
            scored_at=now,
            propagation_chain=list(evaluation.propagation_chain),
        )
        produced = await self._repo.store_score_with_outbox(scored)
        logger.info(
            "prediction_scored" if produced else "score_duplicate",
            prediction_id=str(evaluation.prediction_id),
            asset_id=evaluation.asset_id.value,
            predicted_direction=evaluation.predicted_direction.value,
            actual_direction=outcome.actual_direction.value,
            actual_return=round(outcome.actual_return, 5),
            is_correct=outcome.is_correct,
        )
