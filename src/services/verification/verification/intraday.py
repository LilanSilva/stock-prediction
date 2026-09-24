"""Isolated shadow evaluations. No PredictionScored publication and no learning side effects."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import structlog
from shared.reference import resolve
from shared.schemas.messages import IntradayBar, IntradayObserved, IntradayRequested, PredictionMade

from verification.config import VerificationSettings
from verification.intraday_policy import (
    IntradayPolicy,
    IntradayResult,
    SessionWindow,
    evaluate,
    resolve_window,
)

logger = structlog.get_logger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS verification.intraday_streams (
    stream_id UUID PRIMARY KEY, request TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification.intraday_batches (
    stream_id UUID NOT NULL REFERENCES verification.intraday_streams(stream_id),
    revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(stream_id,revision)
);
CREATE TABLE IF NOT EXISTS verification.intraday_withdrawals (
    prediction_id UUID PRIMARY KEY, superseded_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS verification.intraday_evaluations (
    prediction_id UUID PRIMARY KEY, prediction TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL,
    stream_id UUID REFERENCES verification.intraday_streams(stream_id),
    session_window TEXT, policy TEXT NOT NULL, policy_hash TEXT NOT NULL,
    registry_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', result TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intraday_evaluations_pending
    ON verification.intraday_evaluations(stream_id) WHERE status='PENDING';
"""


class IntradayVerification:
    def __init__(self, pool: asyncpg.Pool, settings: VerificationSettings) -> None:
        self.pool, self.settings = pool, settings

    async def register(self, prediction: PredictionMade) -> None:
        if self.settings.intraday_mode != "SHADOW":
            return
        series = resolve(prediction.asset_id)
        calendar = self.settings.intraday_calendars.get(str(prediction.asset_id))
        policy = IntradayPolicy(
            target_return=Decimal(str(self.settings.intraday_target_return)),
            neutral_band=Decimal(str(self.settings.intraday_neutral_band)),
            min_minutes=self.settings.intraday_min_minutes,
            max_baseline_delay_seconds=self.settings.intraday_max_baseline_delay_seconds,
        )
        window: SessionWindow | None = None
        result: IntradayResult | None = None
        if calendar:
            try:
                window = resolve_window(prediction.decision_at, calendar, series.timezone)
            except (ValueError, KeyError):
                result = IntradayResult(status="UNSCORABLE", reason="UNSUPPORTED_CALENDAR")
        stream: IntradayRequested | None = None
        now = datetime.now(UTC)
        if window:
            identity = (
                f"intraday-v1|{prediction.asset_id}|{series.registry_version}|{calendar}|"
                f"{window.opens_at.isoformat()}|{window.closes_at.isoformat()}"
            )
            stream = IntradayRequested(
                stream_id=uuid.uuid5(uuid.NAMESPACE_URL, identity),
                correlation_id=prediction.correlation_id,
                causation_id=prediction.message_id,
                occurred_at=now,
                asset_id=prediction.asset_id,
                registry_version=series.registry_version,
                calendar_id=window.calendar_id,
                opens_at=window.opens_at,
                closes_at=window.closes_at,
            )
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if prediction.supersedes_prediction_id:
                    # Tombstone handles reversed message order. Once trading started, preserve
                    # measurement; a replaced pre-open stance has no independent price outcome.
                    await conn.execute(
                        "INSERT INTO verification.intraday_withdrawals VALUES($1,$2) "
                        "ON CONFLICT(prediction_id) DO UPDATE SET superseded_at="
                        "LEAST(intraday_withdrawals.superseded_at, EXCLUDED.superseded_at)",
                        prediction.supersedes_prediction_id,
                        prediction.decision_at,
                    )
                    withdrawn = IntradayResult(status="WITHDRAWN", reason="SUPERSEDED_BEFORE_OPEN")
                    await conn.execute(
                        "UPDATE verification.intraday_evaluations "
                        "SET status='WITHDRAWN', result=$3, updated_at=now() "
                        "WHERE prediction_id=$1 AND "
                        "(session_window::jsonb->>'opens_at')::timestamptz >= $2",
                        prediction.supersedes_prediction_id,
                        prediction.decision_at,
                        withdrawn.model_dump_json(),
                    )
                if not calendar:
                    return
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                    str(prediction.prediction_id),
                )
                if await conn.fetchval(
                    "SELECT 1 FROM verification.intraday_evaluations WHERE prediction_id=$1",
                    prediction.prediction_id,
                ):
                    return
                if stream:
                    inserted = await conn.fetchval(
                        "INSERT INTO verification.intraday_streams VALUES($1,$2) "
                        "ON CONFLICT DO NOTHING RETURNING stream_id",
                        stream.stream_id,
                        stream.model_dump_json(),
                    )
                    if inserted:
                        await conn.execute(
                            "INSERT INTO verification.outbox_events "
                            "(message_id,aggregate_id,message_type,payload) "
                            "VALUES($1,$2,'IntradayRequested',$3)",
                            stream.message_id,
                            stream.stream_id,
                            stream.model_dump_json(),
                        )
                await conn.execute(
                    "INSERT INTO verification.intraday_evaluations "
                    "(prediction_id,prediction,received_at,stream_id,session_window,policy,policy_hash,"
                    "registry_version,status,result) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                    "ON CONFLICT DO NOTHING",
                    prediction.prediction_id,
                    prediction.model_dump_json(),
                    now,
                    stream.stream_id if stream else None,
                    window.model_dump_json() if window else None,
                    policy.model_dump_json(),
                    hashlib.sha256(policy.model_dump_json().encode()).hexdigest(),
                    series.registry_version,
                    result.status if result else "PENDING",
                    result.model_dump_json() if result else None,
                )

    async def observe(self, message: IntradayObserved) -> None:
        raw = await self.pool.fetchval(
            "SELECT request FROM verification.intraday_streams WHERE stream_id=$1",
            message.stream_id,
        )
        if raw is None:
            # Same orphan policy as daily observations: removed work cannot be recovered by retry.
            logger.warning("intraday_observation_orphaned", stream_id=str(message.stream_id))
            return
        stream = IntradayRequested.model_validate_json(raw)
        if (
            message.asset_id != stream.asset_id
            or message.registry_version != stream.registry_version
        ):
            raise ValueError("intraday stream identity mismatch")
        if any(not stream.opens_at <= b.start < stream.closes_at for b in message.bars):
            raise ValueError("intraday bar outside requested session")
        if any(b.start + timedelta(minutes=1) > message.occurred_at for b in message.bars):
            raise ValueError("intraday bar not yet complete at observation time")
        if message.final and not message.failure and message.occurred_at < stream.closes_at:
            raise ValueError("final intraday observation before session close")
        if len({b.start for b in message.bars}) != len(message.bars):
            raise ValueError("duplicate intraday bar timestamp")
        inserted = await self.pool.fetchval(
            "INSERT INTO verification.intraday_batches VALUES($1,$2,$3) "
            "ON CONFLICT DO NOTHING RETURNING revision",
            message.stream_id,
            message.revision,
            message.model_dump_json(),
        )
        if inserted is None:
            prior = IntradayObserved.model_validate_json(
                await self.pool.fetchval(
                    "SELECT payload FROM verification.intraday_batches "
                    "WHERE stream_id=$1 AND revision=$2",
                    message.stream_id,
                    message.revision,
                )
            )
            if (prior.bars, prior.final, prior.failure) != (
                message.bars,
                message.final,
                message.failure,
            ):
                raise ValueError("conflicting intraday revision")

    async def tick(self) -> None:
        # Database locks cover only local computation. No HTTP or broker calls inside transactions.
        rows = await self.pool.fetch(
            "SELECT prediction_id FROM verification.intraday_evaluations WHERE status='PENDING' "
            "ORDER BY updated_at LIMIT 500"
        )
        for row in rows:
            await self.score_one(row["prediction_id"])

    async def score_one(self, prediction_id: uuid.UUID) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM verification.intraday_evaluations "
                    "WHERE prediction_id=$1 AND status='PENDING' FOR UPDATE",
                    prediction_id,
                )
                if row is None:
                    return
                prediction = PredictionMade.model_validate_json(row["prediction"])
                window = SessionWindow.model_validate_json(row["session_window"])
                policy = IntradayPolicy.model_validate_json(row["policy"])
                superseded = await conn.fetchval(
                    "SELECT superseded_at FROM verification.intraday_withdrawals "
                    "WHERE prediction_id=$1",
                    prediction_id,
                )
                if superseded is not None and superseded <= window.opens_at:
                    result = IntradayResult(status="WITHDRAWN", reason="SUPERSEDED_BEFORE_OPEN")
                else:
                    batches = await conn.fetch(
                        "SELECT payload FROM verification.intraday_batches "
                        "WHERE stream_id=$1 ORDER BY revision",
                        row["stream_id"],
                    )
                    bars: dict[datetime, IntradayBar] = {}
                    final = False
                    failure = None
                    expected_revision = 1
                    for batch in batches:
                        observation = IntradayObserved.model_validate_json(batch["payload"])
                        if observation.revision != expected_revision:
                            # FINAL can arrive before a missing durable delta.
                            break
                        expected_revision += 1
                        bars.update({b.start: b for b in observation.bars})
                        if observation.final:
                            final, failure = True, observation.failure
                            break
                    if datetime.now(UTC) > window.closes_at + timedelta(hours=50) and not final:
                        final, failure = True, "OBSERVATION_DEADLINE_EXPIRED"
                    result = evaluate(
                        decision_at=prediction.decision_at,
                        window=window,
                        direction=prediction.direction,
                        policy=policy,
                        bars=list(bars.values()),
                        final=final,
                        failure=failure,
                        previous=IntradayResult.model_validate_json(row["result"])
                        if row["result"]
                        else None,
                    )
                await conn.execute(
                    "UPDATE verification.intraday_evaluations SET status=$2, result=$3, "
                    "updated_at=now() WHERE prediction_id=$1",
                    prediction_id,
                    result.status,
                    result.model_dump_json(),
                )

    async def report(self, prediction_id: uuid.UUID) -> dict[str, object] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM verification.intraday_evaluations WHERE prediction_id=$1",
            prediction_id,
        )
        if row is None:
            return None
        prediction = PredictionMade.model_validate_json(row["prediction"])
        return {
            "prediction_id": str(prediction_id),
            "asset_id": str(prediction.asset_id),
            "stream_id": str(row["stream_id"]) if row["stream_id"] else None,
            "registry_version": row["registry_version"],
            "predicted_direction": prediction.direction.value,
            "mode": "SHADOW",
            "status": row["status"],
            "decision_at": prediction.decision_at.isoformat(),
            "publication_attempt_at": prediction.publication_attempt_at.isoformat()
            if prediction.publication_attempt_at
            else None,
            "received_at": row["received_at"].isoformat(),
            "policy": json.loads(row["policy"]),
            "policy_hash": row["policy_hash"],
            "window": json.loads(row["session_window"]) if row["session_window"] else None,
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    async def summary(self) -> list[dict[str, object]]:
        """Keep policy cohorts and unscorable outcomes separate in the rollout comparison."""
        rows = await self.pool.fetch(
            "SELECT e.policy_hash, e.status, count(*) AS evaluations, "
            "count(*) FILTER (WHERE (e.result::jsonb->>'target_reached')::boolean) AS hits, "
            "count(*) FILTER (WHERE s.is_correct) AS daily_correct, "
            "count(s.prediction_id) AS daily_scored "
            "FROM verification.intraday_evaluations e LEFT JOIN verification.scores s "
            "USING(prediction_id) GROUP BY e.policy_hash,e.status ORDER BY e.policy_hash,e.status"
        )
        return [dict(row) for row in rows]
