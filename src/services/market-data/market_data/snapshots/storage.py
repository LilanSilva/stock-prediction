"""Additive durable jobs, immutable observations and transactional publication."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from shared.schemas.messages import PriceSampleObserved

from market_data.snapshots.calendar import Session, slots
from market_data.snapshots.config import Listing, MappingFile

DDL = """
CREATE TABLE IF NOT EXISTS market_data.snapshot_mappings (
 version TEXT PRIMARY KEY, content_hash TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_data.snapshot_assets (
 asset_id TEXT PRIMARY KEY, version TEXT NOT NULL, enabled BOOLEAN NOT NULL,
 paused BOOLEAN NOT NULL DEFAULT false, failures INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, last_success TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS market_data.snapshot_control (
 id INTEGER PRIMARY KEY CHECK(id=1), cooldown_until TIMESTAMPTZ,
 paused BOOLEAN NOT NULL DEFAULT false, failure_count INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'STARTING', heartbeat TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO market_data.snapshot_control(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS market_data.snapshot_sessions (
 asset_id TEXT NOT NULL, session DATE NOT NULL, version TEXT NOT NULL,
 session_window TEXT NOT NULL, market_state TEXT, state_at TIMESTAMPTZ,
 status_text TEXT, final_sample UUID, close_status TEXT NOT NULL DEFAULT 'PENDING',
 PRIMARY KEY(asset_id,session,version)
);
CREATE TABLE IF NOT EXISTS market_data.snapshot_jobs (
 job_id UUID PRIMARY KEY, asset_id TEXT NOT NULL, session DATE NOT NULL,
 version TEXT NOT NULL REFERENCES market_data.snapshot_mappings(version),
 registry_version TEXT NOT NULL, listing TEXT NOT NULL, session_window TEXT NOT NULL,
 scheduled_at TIMESTAMPTZ NOT NULL, kind TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMPTZ NOT NULL, lease UUID, lease_until TIMESTAMPTZ,
 last_error TEXT, UNIQUE(asset_id,session,version,scheduled_at,kind)
);
CREATE INDEX IF NOT EXISTS snapshot_jobs_due ON market_data.snapshot_jobs(next_attempt_at)
 WHERE state='PENDING';
CREATE TABLE IF NOT EXISTS market_data.price_samples (
 sample_id UUID PRIMARY KEY REFERENCES market_data.snapshot_jobs(job_id),
 asset_id TEXT NOT NULL, session DATE NOT NULL,
 mapping_version TEXT NOT NULL REFERENCES market_data.snapshot_mappings(version),
 registry_version TEXT NOT NULL, instrument_id TEXT NOT NULL, exchange TEXT NOT NULL,
 price NUMERIC NOT NULL CHECK(price>0), currency TEXT NOT NULL, quote_unit TEXT NOT NULL,
 scheduled_at TIMESTAMPTZ NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
 provider_quote_at TIMESTAMPTZ, kind TEXT NOT NULL, quality TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS price_samples_asset_time
 ON market_data.price_samples(asset_id,observed_at);
CREATE TABLE IF NOT EXISTS market_data.snapshot_outbox (
 message_id UUID PRIMARY KEY, payload TEXT NOT NULL, delivered BOOLEAN NOT NULL DEFAULT false,
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class SnapshotStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def install(self, config: MappingFile) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO market_data.snapshot_mappings VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                config.mapping_version,
                config.content_hash,
                config.model_dump_json(),
            )
            existing = await conn.fetchval(
                "SELECT content_hash FROM market_data.snapshot_mappings WHERE version=$1",
                config.mapping_version,
            )
            if existing != config.content_hash:
                raise ValueError("mapping changed without a version change")
            await conn.execute("UPDATE market_data.snapshot_assets SET enabled=false")
            for listing in config.listings:
                await conn.execute(
                    "INSERT INTO market_data.snapshot_assets(asset_id,version,enabled) "
                    "VALUES($1,$2,$3) ON CONFLICT(asset_id) DO UPDATE SET "
                    "enabled=EXCLUDED.enabled, version=EXCLUDED.version, "
                    "paused=CASE WHEN snapshot_assets.version=EXCLUDED.version "
                    "THEN snapshot_assets.paused ELSE false END, "
                    "failures=CASE WHEN snapshot_assets.version=EXCLUDED.version "
                    "THEN snapshot_assets.failures ELSE 0 END",
                    listing.asset_id,
                    config.mapping_version,
                    listing.enabled,
                )
            await conn.execute(
                "UPDATE market_data.snapshot_jobs j SET state='CANCELLED', lease=NULL, "
                "last_error='MAPPING_DISABLED' WHERE state='PENDING' AND NOT EXISTS "
                "(SELECT 1 FROM market_data.snapshot_assets a WHERE a.asset_id=j.asset_id "
                "AND a.version=j.version AND a.enabled AND NOT a.paused)"
            )

    async def schedule(
        self, listing: Listing, version: str, session_window: Session, now: datetime
    ) -> None:
        registry_version = listing.check_registry()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO market_data.snapshot_sessions"
                "(asset_id,session,version,session_window) "
                "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                listing.asset_id,
                session_window.session,
                version,
                session_window.model_dump_json(),
            )
            for slot in slots(session_window):
                key = f"{listing.asset_id}|{version}|{slot.kind}|{slot.scheduled_at.isoformat()}"
                await conn.execute(
                    "INSERT INTO market_data.snapshot_jobs "
                    "(job_id,asset_id,session,version,registry_version,listing,session_window,scheduled_at,"
                    "kind,next_attempt_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$8) "
                    "ON CONFLICT DO NOTHING",
                    uuid.uuid5(uuid.NAMESPACE_URL, key),
                    listing.asset_id,
                    session_window.session,
                    version,
                    registry_version,
                    listing.model_dump_json(),
                    session_window.model_dump_json(),
                    slot.scheduled_at,
                    slot.kind,
                )

    async def expire(self, now: datetime, lateness: int) -> None:
        await self.pool.execute(
            "UPDATE market_data.snapshot_jobs SET state='MISSED', last_error='MISSED_DEADLINE', "
            "lease=NULL WHERE state='PENDING' AND scheduled_at<$1 "
            "AND (lease_until IS NULL OR lease_until<$2)",
            now - timedelta(seconds=lateness),
            now,
        )
        await self.pool.execute(
            "UPDATE market_data.snapshot_sessions SET close_status='CLOSE_UNCONFIRMED' "
            "WHERE close_status='PENDING' AND "
            "(session_window::jsonb->>'closes_at')::timestamptz + interval '7 minutes' < $1",
            now,
        )

    async def claim(self, now: datetime) -> Any:
        token = uuid.uuid4()
        return await self.pool.fetchrow(
            "UPDATE market_data.snapshot_jobs SET lease=$1,lease_until=$2,attempts=attempts+1 "
            "WHERE job_id=(SELECT j.job_id FROM market_data.snapshot_jobs j "
            "JOIN market_data.snapshot_assets a ON a.asset_id=j.asset_id AND a.version=j.version "
            "WHERE j.state='PENDING' AND j.next_attempt_at<=$3 AND a.enabled AND NOT a.paused "
            "AND (j.lease_until IS NULL OR j.lease_until<$3) "
            "ORDER BY j.next_attempt_at FOR UPDATE OF j SKIP LOCKED LIMIT 1) RETURNING *",
            token,
            now + timedelta(seconds=90),
            now,
        )

    async def failure(
        self,
        row: Any,
        reason: str,
        now: datetime,
        *,
        retry: bool = False,
        pause: bool = False,
        retry_seconds: float = 10,
    ) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            owned = await conn.fetchval(
                "UPDATE market_data.snapshot_jobs SET state=$3,last_error=$4,lease=NULL,"
                "lease_until=NULL,next_attempt_at=$5 WHERE job_id=$1 AND lease=$2 "
                "AND state='PENDING' RETURNING asset_id",
                row["job_id"],
                row["lease"],
                "PENDING" if retry else "FAILED",
                reason,
                now + timedelta(seconds=retry_seconds),
            )
            if owned and not retry:
                await conn.execute(
                    "UPDATE market_data.snapshot_assets SET failures=failures+1,last_error=$2,"
                    "paused=paused OR $3 OR ($2='PARSE_FAILED' AND failures>=2) "
                    "WHERE asset_id=$1",
                    owned,
                    reason,
                    pause,
                )

    async def save(self, row: Any, message: PriceSampleObserved, status_text: str) -> bool:
        async with self.pool.acquire() as conn, conn.transaction():
            owned = await conn.fetchval(
                "UPDATE market_data.snapshot_jobs j SET state='SUCCEEDED',lease=NULL,"
                "lease_until=NULL,last_error=NULL WHERE job_id=$1 AND lease=$2 "
                "AND state='PENDING' AND lease_until>=now() AND EXISTS "
                "(SELECT 1 FROM market_data.snapshot_assets a WHERE a.asset_id=j.asset_id "
                "AND a.version=j.version AND a.enabled AND NOT a.paused) RETURNING job_id",
                row["job_id"],
                row["lease"],
            )
            if owned is None:
                return False
            listing = Listing.model_validate_json(row["listing"])
            await conn.execute(
                "INSERT INTO market_data.price_samples VALUES "
                "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)",
                message.sample_id,
                str(message.asset_id),
                message.session,
                message.mapping_version,
                message.registry_version,
                listing.instrument_id,
                listing.expected_exchange,
                message.price,
                message.currency,
                message.quote_unit,
                message.scheduled_at,
                message.observed_at,
                message.provider_quote_at,
                message.kind,
                message.quality,
                message.model_dump_json(),
            )
            await conn.execute(
                "INSERT INTO market_data.snapshot_outbox(message_id,payload) VALUES($1,$2)",
                message.message_id,
                message.model_dump_json(),
            )
            await conn.execute(
                "UPDATE market_data.snapshot_assets SET failures=0,last_error=NULL,"
                "last_success=$2 WHERE asset_id=$1",
                str(message.asset_id),
                message.observed_at,
            )
            await conn.execute(
                "UPDATE market_data.snapshot_sessions SET market_state=$4,"
                "state_at=$5,status_text=$6,"
                "final_sample=CASE WHEN $7 THEN $8 ELSE final_sample END,"
                "close_status=CASE WHEN $7 THEN 'CLOSE_UNCONFIRMED' ELSE close_status END "
                "WHERE asset_id=$1 AND session=$2 AND version=$3",
                str(message.asset_id),
                message.session,
                message.mapping_version,
                message.market_state,
                message.observed_at,
                status_text,
                message.kind == "CLOSE_CHECK",
                message.sample_id,
            )
            return True

    async def control(
        self, status: str, now: datetime, *, pause: bool = False, cooldown: int = 0
    ) -> None:
        await self.pool.execute(
            "UPDATE market_data.snapshot_control SET status=$1,heartbeat=$2,paused=paused OR $3,"
            "cooldown_until=CASE WHEN $4>0 THEN GREATEST(cooldown_until,$2+"
            "($4*interval '1 second')) ELSE cooldown_until END WHERE id=1",
            status,
            now,
            pause,
            cooldown,
        )

    async def provider_failure(self, now: datetime) -> None:
        await self.pool.execute(
            "UPDATE market_data.snapshot_control SET failure_count=failure_count+1,"
            "cooldown_until=CASE WHEN failure_count>=4 THEN $1+interval '15 minutes' "
            "ELSE cooldown_until END WHERE id=1",
            now,
        )


async def status(pool: asyncpg.Pool) -> dict[str, object]:
    if not await pool.fetchval("SELECT to_regclass('market_data.snapshot_control')"):
        return {"status": "NOT_STARTED", "assets": []}
    rows = await pool.fetch(
        "SELECT a.*,s.session_window,s.market_state,s.state_at,s.close_status FROM "
        "market_data.snapshot_assets a LEFT JOIN LATERAL "
        "(SELECT * FROM market_data.snapshot_sessions x WHERE x.asset_id=a.asset_id "
        "AND x.version=a.version ORDER BY session DESC LIMIT 1) s ON true ORDER BY a.asset_id"
    )
    assets = []
    for row in rows:
        item = dict(row)
        if item["session_window"]:
            item["session_window"] = json.loads(item["session_window"])
        assets.append(item)
    return {
        "control": dict(
            await pool.fetchrow("SELECT * FROM market_data.snapshot_control WHERE id=1")
        ),
        "assets": assets,
        "jobs": [
            dict(r)
            for r in await pool.fetch(
                "SELECT state,count(*) AS count FROM market_data.snapshot_jobs GROUP BY state"
            )
        ],
        "pending_events": await pool.fetchval(
            "SELECT count(*) FROM market_data.snapshot_outbox WHERE NOT delivered"
        ),
    }
