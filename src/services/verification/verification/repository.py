"""PostgreSQL repository for evaluations, price observations, scores, and the outbox."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg
from shared.schemas.messages import (
    ContributingEdge,
    PredictionScored,
    PriceObserved,
    PriceRequested,
)

from verification.models import EvaluationRecord, EvaluationStatus

_PRICE_REQUESTED = "PriceRequested"
_PREDICTION_SCORED = "PredictionScored"


def _edges_to_json(edges: list[ContributingEdge]) -> str:
    return json.dumps([edge.model_dump(mode="json") for edge in edges])


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, str):
        loaded = json.loads(raw)
        return list(loaded) if isinstance(loaded, list) else []
    return list(raw) if isinstance(raw, list) else []


class VerificationRepository:
    """Owns all reads/writes for the ``verification`` schema."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_evaluation_with_outbox(
        self, evaluation: EvaluationRecord, request: PriceRequested
    ) -> bool:
        """Insert the evaluation and outbox the PriceRequested in one transaction.

        Returns False when the prediction was already evaluated (duplicate delivery), so no second
        evaluation or price request is created.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO verification.evaluations
                        (prediction_id, context_id, asset_id, predicted_direction,
                         predicted_magnitude, confidence, decision_at, baseline_session,
                         settlement_session, market_calendar, registry_version, request_id,
                         correlation_id, contributing_edges, source_ids, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                            $14::jsonb, $15::jsonb, 'PENDING')
                    ON CONFLICT (prediction_id) DO NOTHING
                    RETURNING prediction_id
                    """,
                    evaluation.prediction_id,
                    evaluation.context_id,
                    evaluation.asset_id.value,
                    evaluation.predicted_direction.value,
                    evaluation.predicted_magnitude.value,
                    evaluation.confidence,
                    evaluation.decision_at,
                    evaluation.baseline_session,
                    evaluation.settlement_session,
                    evaluation.market_calendar,
                    evaluation.registry_version,
                    evaluation.request_id,
                    evaluation.correlation_id,
                    _edges_to_json(evaluation.contributing_edges),
                    json.dumps(evaluation.source_ids),
                )
                if inserted is None:
                    return False

                await conn.execute(
                    """
                    INSERT INTO verification.outbox_events
                        (message_id, aggregate_id, message_type, payload)
                    VALUES ($1, $2, $3, $4)
                    """,
                    request.message_id,
                    evaluation.prediction_id,
                    _PRICE_REQUESTED,
                    request.model_dump_json(),
                )
                return True

    async def store_price_observation(self, message: PriceObserved) -> bool:
        """Persist the dual-close observation (idempotent by request_id)."""
        inserted = await self._pool.fetchval(
            """
            INSERT INTO verification.price_observations
                (request_id, prediction_id, asset_id, baseline, settlement)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
            ON CONFLICT (request_id) DO NOTHING
            RETURNING request_id
            """,
            message.request_id,
            message.prediction_id,
            message.asset_id.value,
            message.baseline.model_dump_json(),
            message.settlement.model_dump_json(),
        )
        return inserted is not None

    async def load_evaluation_by_request_id(
        self, request_id: uuid.UUID
    ) -> EvaluationRecord | None:
        row = await self._pool.fetchrow(
            """
            SELECT prediction_id, context_id, asset_id, predicted_direction, predicted_magnitude,
                   confidence, decision_at, baseline_session, settlement_session, market_calendar,
                   registry_version, request_id, correlation_id, contributing_edges, source_ids,
                   status
            FROM verification.evaluations
            WHERE request_id = $1
            """,
            request_id,
        )
        if row is None:
            return None
        from shared.schemas.messages import AssetId, Direction, Magnitude

        return EvaluationRecord(
            prediction_id=row["prediction_id"],
            context_id=row["context_id"],
            asset_id=AssetId(row["asset_id"]),
            predicted_direction=Direction(row["predicted_direction"]),
            predicted_magnitude=Magnitude(row["predicted_magnitude"]),
            confidence=row["confidence"],
            decision_at=row["decision_at"],
            baseline_session=row["baseline_session"],
            settlement_session=row["settlement_session"],
            market_calendar=row["market_calendar"],
            registry_version=row["registry_version"],
            request_id=row["request_id"],
            correlation_id=row["correlation_id"],
            contributing_edges=[
                ContributingEdge.model_validate(item)
                for item in _json_list(row["contributing_edges"])
            ],
            source_ids=[str(item) for item in _json_list(row["source_ids"])],
            status=EvaluationStatus(row["status"]),
        )

    async def withdraw_evaluation(self, prediction_id: uuid.UUID) -> bool:
        """Mark a superseded prediction's evaluation WITHDRAWN so it is never scored.

        Returns True when a PENDING evaluation was withdrawn; a missing or already-terminal
        evaluation is a no-op (the collapse signal is idempotent).
        """
        result = await self._pool.execute(
            "UPDATE verification.evaluations SET status = 'WITHDRAWN', updated_at = now() "
            "WHERE prediction_id = $1 AND status = 'PENDING'",
            prediction_id,
        )
        return str(result).endswith("1")

    async def store_score_with_outbox(self, scored: PredictionScored) -> bool:
        """Insert the score and outbox the PredictionScored in one transaction.

        Returns False when the prediction was already scored (duplicate PriceObserved), so no second
        evidence identity is published.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO verification.scores
                        (prediction_id, asset_id, predicted_direction, actual_direction,
                         predicted_magnitude, actual_magnitude, confidence, actual_return,
                         is_correct, score, scored_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (prediction_id) DO NOTHING
                    RETURNING prediction_id
                    """,
                    scored.prediction_id,
                    scored.asset_id.value,
                    scored.predicted_direction.value,
                    scored.actual_direction.value,
                    scored.predicted_magnitude.value,
                    scored.actual_magnitude.value,
                    scored.confidence,
                    scored.actual_return,
                    scored.is_correct,
                    scored.score,
                    scored.scored_at,
                )
                if inserted is None:
                    await conn.execute(
                        "UPDATE verification.evaluations SET status = 'SCORED', updated_at = now() "
                        "WHERE prediction_id = $1",
                        scored.prediction_id,
                    )
                    return False

                await conn.execute(
                    """
                    INSERT INTO verification.outbox_events
                        (message_id, aggregate_id, message_type, payload)
                    VALUES ($1, $2, $3, $4)
                    """,
                    scored.message_id,
                    scored.prediction_id,
                    _PREDICTION_SCORED,
                    scored.model_dump_json(),
                )
                await conn.execute(
                    "UPDATE verification.evaluations SET status = 'SCORED', updated_at = now() "
                    "WHERE prediction_id = $1",
                    scored.prediction_id,
                )
                return True
