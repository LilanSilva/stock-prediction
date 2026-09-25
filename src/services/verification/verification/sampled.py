"""Durable independent shadow consumer; publishes no scoring or learning events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
from shared.reference import resolve
from shared.schemas.messages import PredictionMade, PriceSampleObserved

from verification.config import VerificationSettings
from verification.intraday_policy import SessionWindow, resolve_window
from verification.sampled_policy import SamplePolicy, SampleResult, evaluate_samples

DDL = """
CREATE TABLE IF NOT EXISTS verification.sample_evidence (
 sample_id UUID PRIMARY KEY, asset_id TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
 payload TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sample_evidence_asset_time
 ON verification.sample_evidence(asset_id,observed_at);
CREATE TABLE IF NOT EXISTS verification.sample_evaluations (
 prediction_id UUID PRIMARY KEY, asset_id TEXT NOT NULL, prediction TEXT NOT NULL,
 session_window TEXT NOT NULL, policy TEXT NOT NULL,
 currency TEXT NOT NULL, registry_version TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING', result TEXT,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS verification.sample_withdrawals (
 prediction_id UUID PRIMARY KEY, superseded_at TIMESTAMPTZ NOT NULL
);
"""


class SampleVerification:
    def __init__(self, pool: asyncpg.Pool, settings: VerificationSettings) -> None:
        self.pool, self.settings = pool, settings

    async def register(self, prediction: PredictionMade) -> None:
        if self.settings.sample_mode != "SHADOW":
            return
        calendar = self.settings.sample_calendars.get(str(prediction.asset_id))
        async with self.pool.acquire() as conn, conn.transaction():
            if prediction.supersedes_prediction_id:
                await conn.execute(
                    "INSERT INTO verification.sample_withdrawals VALUES($1,$2) "
                    "ON CONFLICT(prediction_id) DO UPDATE SET superseded_at="
                    "LEAST(sample_withdrawals.superseded_at,EXCLUDED.superseded_at)",
                    prediction.supersedes_prediction_id,
                    prediction.decision_at,
                )
                await conn.execute(
                    "UPDATE verification.sample_evaluations SET status='WITHDRAWN',result=$3 "
                    "WHERE prediction_id=$1 "
                    "AND (session_window::jsonb->>'opens_at')::timestamptz >=$2",
                    prediction.supersedes_prediction_id,
                    prediction.decision_at,
                    SampleResult(
                        status="WITHDRAWN", outcome="SUPERSEDED_BEFORE_OPEN"
                    ).model_dump_json(),
                )
            if not calendar:
                return
            series = resolve(prediction.asset_id)
            session_window = resolve_window(prediction.decision_at, calendar, series.timezone)
            policy = SamplePolicy(
                target_return=Decimal(str(self.settings.sample_target_return)),
                neutral_band=Decimal(str(self.settings.sample_neutral_band)),
            )
            await conn.execute(
                "INSERT INTO verification.sample_evaluations "
                "(prediction_id,asset_id,prediction,session_window,policy,"
                "currency,registry_version) "
                "VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING",
                prediction.prediction_id,
                str(prediction.asset_id),
                prediction.model_dump_json(),
                session_window.model_dump_json(),
                policy.model_dump_json(),
                series.currency,
                series.registry_version,
            )

    async def observe(self, sample: PriceSampleObserved) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO verification.sample_evidence(sample_id,asset_id,observed_at,payload) "
                "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                sample.sample_id,
                str(sample.asset_id),
                sample.observed_at,
                sample.model_dump_json(),
            )
            payload = await conn.fetchval(
                "SELECT payload FROM verification.sample_evidence WHERE sample_id=$1",
                sample.sample_id,
            )
            if PriceSampleObserved.model_validate_json(payload) != sample:
                raise ValueError("conflicting immutable sample")

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        ids = await self.pool.fetch(
            "SELECT prediction_id FROM verification.sample_evaluations WHERE status='PENDING' "
            "ORDER BY updated_at LIMIT 100"
        )
        for identity in ids:
            async with self.pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM verification.sample_evaluations WHERE prediction_id=$1 "
                    "AND status='PENDING' FOR UPDATE SKIP LOCKED",
                    identity["prediction_id"],
                )
                if not row:
                    continue
                session_window = SessionWindow.model_validate_json(row["session_window"])
                prediction = PredictionMade.model_validate_json(row["prediction"])
                policy = SamplePolicy.model_validate_json(row["policy"])
                final_at = session_window.closes_at + timedelta(
                    seconds=policy.delivery_grace_seconds
                )
                samples = await conn.fetch(
                    "SELECT payload FROM verification.sample_evidence WHERE asset_id=$1 "
                    "AND observed_at>=$2 AND observed_at<$3 AND received_at<=$4",
                    row["asset_id"],
                    session_window.opens_at,
                    session_window.closes_at,
                    final_at,
                )
                result = evaluate_samples(
                    prediction.decision_at,
                    session_window,
                    prediction.direction,
                    policy,
                    row["currency"],
                    row["registry_version"],
                    [PriceSampleObserved.model_validate_json(s["payload"]) for s in samples],
                    final=now >= final_at,
                    previous=SampleResult.model_validate_json(row["result"])
                    if row["result"]
                    else None,
                )
                withdrawal = await conn.fetchval(
                    "SELECT superseded_at FROM verification.sample_withdrawals "
                    "WHERE prediction_id=$1",
                    prediction.prediction_id,
                )
                if withdrawal and withdrawal <= session_window.opens_at:
                    result = SampleResult(status="WITHDRAWN", outcome="SUPERSEDED_BEFORE_OPEN")
                await conn.execute(
                    "UPDATE verification.sample_evaluations SET result=$2,status=$3,updated_at=$4 "
                    "WHERE prediction_id=$1",
                    prediction.prediction_id,
                    result.model_dump_json(),
                    result.status,
                    now,
                )

    async def get(self, prediction_id: uuid.UUID) -> dict[str, object] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM verification.sample_evaluations WHERE prediction_id=$1", prediction_id
        )
        return dict(row) if row else None
