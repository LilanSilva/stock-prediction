"""Durable per-session collection, short leases, delta publication, bounded reconciliation."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import structlog
from shared.messaging.client import RabbitMQClient
from shared.reference import AssetReferenceSeries, resolve
from shared.schemas.messages import IntradayBar, IntradayObserved, IntradayRequested

from market_data.config import MarketDataSettings
from market_data.exceptions import InvalidObservationError
from market_data.intraday_adapter import IntradayAdapter

logger = structlog.get_logger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS market_data.intraday_streams (
    stream_id UUID PRIMARY KEY, request TEXT NOT NULL, series TEXT NOT NULL,
    bars JSONB NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
    done BOOLEAN NOT NULL DEFAULT false, lease UUID, next_attempt_at TIMESTAMPTZ NOT NULL,
    last_error TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intraday_streams_due
    ON market_data.intraday_streams(next_attempt_at) WHERE NOT done;
CREATE TABLE IF NOT EXISTS market_data.intraday_outbox (
    message_id UUID PRIMARY KEY, payload TEXT NOT NULL,
    delivered BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class IntradayCollector:
    def __init__(
        self,
        pool: asyncpg.Pool,
        rabbit: RabbitMQClient,
        adapter: IntradayAdapter,
        settings: MarketDataSettings,
    ) -> None:
        self.pool, self.rabbit, self.adapter, self.settings = pool, rabbit, adapter, settings

    async def register(self, request: IntradayRequested) -> None:
        series = resolve(request.asset_id)
        if series.provider != "yahoo" or series.registry_version != request.registry_version:
            raise ValueError("intraday request provider/registry mismatch")
        await self.pool.execute(
            "INSERT INTO market_data.intraday_streams(stream_id, request, next_attempt_at,series) "
            "VALUES($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            request.stream_id,
            request.model_dump_json(),
            request.opens_at,
            series.model_dump_json(),
        )

    async def publish_pending(self) -> None:
        for row in await self.pool.fetch(
            "SELECT message_id, payload FROM market_data.intraday_outbox "
            "WHERE NOT delivered ORDER BY created_at LIMIT 100"
        ):
            await self.rabbit.publish(IntradayObserved.model_validate_json(row["payload"]))
            await self.pool.execute(
                "UPDATE market_data.intraday_outbox SET delivered=true WHERE message_id=$1",
                row["message_id"],
            )

    async def tick(self) -> None:
        await self._relay()
        # Claim just one at a time; network work never holds a SQL transaction or connection.
        for _ in range(100):
            token = uuid.uuid4()
            row = await self.pool.fetchrow(
                "UPDATE market_data.intraday_streams SET lease=$1, "
                "next_attempt_at=now()+interval '5 minutes' WHERE stream_id=("
                "SELECT stream_id FROM market_data.intraday_streams "
                "WHERE NOT done AND next_attempt_at<=now() ORDER BY next_attempt_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
                token,
            )
            if row is None:
                break
            await self.collect(row, token)
        await self._relay()

    async def _relay(self) -> None:
        try:
            await self.publish_pending()
        except Exception:
            # A broker outage must not prevent collecting expiring provider evidence.
            logger.warning("intraday_outbox_retry")

    async def collect(self, row: Any, token: uuid.UUID) -> None:
        request = IntradayRequested.model_validate_json(row["request"])
        now = datetime.now(UTC)
        deadline = request.closes_at + timedelta(hours=self.settings.intraday_retry_hours)
        final_after = request.closes_at + timedelta(
            seconds=self.settings.intraday_finalization_seconds
        )
        raw = row["bars"]
        stored = json.loads(raw) if isinstance(raw, str) else raw
        previous = {k: IntradayBar.model_validate(v) for k, v in stored.items()}
        failure: str | None = None
        try:
            if now > deadline:
                fetched: list[IntradayBar] = []
                failure = "DATA_DEADLINE_EXPIRED"
            else:
                series = AssetReferenceSeries.model_validate_json(row["series"])
                fetched = await self.adapter.fetch(request, series, now)
        except InvalidObservationError:
            fetched = []
            failure = "INVALID_PROVIDER_DATA"
        except Exception:
            # No provider exception text is persisted: URLs or credentials may be embedded in it.
            await self.pool.execute(
                "UPDATE market_data.intraday_streams SET lease=NULL, "
                "next_attempt_at=$3, last_error='PROVIDER_UNAVAILABLE' "
                "WHERE stream_id=$1 AND lease=$2",
                request.stream_id,
                token,
                now + timedelta(seconds=self.settings.intraday_poll_seconds),
            )
            logger.warning("intraday_fetch_retry", stream_id=str(request.stream_id))
            return
        changed = [b for b in fetched if previous.get(b.start.isoformat()) != b]
        for bar in changed:
            previous[bar.start.isoformat()] = bar
        expected = int((request.closes_at - request.opens_at).total_seconds() / 60)
        complete = len(previous) == expected
        final = bool(failure) or (now >= final_after and complete) or now >= deadline
        if final and not complete and not failure:
            failure = "MISSING_BARS"
        # At/after close refetch the full session until complete or expired. During the day Yahoo's
        # bounded session response also repairs earlier gaps; only changed minutes go on the bus.
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                owned = await conn.fetchval(
                    "SELECT stream_id FROM market_data.intraday_streams "
                    "WHERE stream_id=$1 AND lease=$2 FOR UPDATE",
                    request.stream_id,
                    token,
                )
                if owned is None:
                    return
                revision = int(row["revision"])
                if changed or final:
                    revision += 1
                    observed = IntradayObserved(
                        correlation_id=request.correlation_id,
                        causation_id=request.message_id,
                        occurred_at=now,
                        stream_id=request.stream_id,
                        asset_id=request.asset_id,
                        registry_version=request.registry_version,
                        revision=revision,
                        bars=changed,
                        final=final,
                        failure=failure,
                    )
                    await conn.execute(
                        "INSERT INTO market_data.intraday_outbox(message_id,payload) VALUES($1,$2)",
                        observed.message_id,
                        observed.model_dump_json(),
                    )
                await conn.execute(
                    "UPDATE market_data.intraday_streams SET bars=$3::jsonb, revision=$4, "
                    "done=$5, lease=NULL, next_attempt_at=$6, last_error=$7, updated_at=now() "
                    "WHERE stream_id=$1 AND lease=$2",
                    request.stream_id,
                    token,
                    json.dumps({k: v.model_dump(mode="json") for k, v in previous.items()}),
                    revision,
                    final,
                    now + timedelta(seconds=self.settings.intraday_poll_seconds),
                    failure,
                )
