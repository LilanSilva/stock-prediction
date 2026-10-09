"""Service-owned persistence; browser credentials and conversations are never stored."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg

from .adapters.common.types import REQUEST_FAILURES, Result, Status

DDL = """
CREATE SCHEMA IF NOT EXISTS llm_browser_gateway;
CREATE TABLE IF NOT EXISTS llm_browser_gateway.availability (
  profile_id text NOT NULL, provider text NOT NULL, reason text NOT NULL,
  reset_at timestamptz, next_check_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (profile_id, provider)
);
CREATE TABLE IF NOT EXISTS llm_browser_gateway.attempts (
  attempt_id text PRIMARY KEY, request_id text NOT NULL, profile_id text NOT NULL,
  provider text NOT NULL, state text NOT NULL, outcome text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE llm_browser_gateway.attempts ADD COLUMN IF NOT EXISTS slot integer NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS llm_browser_gateway.gateway_active_resource;
CREATE UNIQUE INDEX IF NOT EXISTS gateway_active_slot
ON llm_browser_gateway.attempts(profile_id, provider, slot)
WHERE state IN ('reserved', 'submitted', 'unknown');
"""


@dataclass(frozen=True)
class Availability:
    reason: str
    reset_at: datetime | None = None
    next_check_at: datetime | None = None

    @property
    def eligible(self) -> bool:
        return self.next_check_at is not None and self.next_check_at <= datetime.now(UTC)


class Store(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def availability(self, profile: str, provider: str) -> Availability | None: ...
    async def reserve(
        self, profile: str, provider: str, attempt: str, request: str, slot: int = 0
    ) -> bool: ...
    async def submitted(self, attempt: str) -> None: ...
    async def finish(self, profile: str, provider: str, attempt: str, result: Result) -> None: ...
    async def terminal(self, profile: str, provider: str, attempt: str) -> None: ...
    async def reset(self, profile: str) -> None: ...
    async def snapshot(self, profile: str) -> dict[str, Any]: ...


class PostgresStore:
    def __init__(self, url: str, retry_seconds: float, retention_days: int) -> None:
        self.url = url
        self.retry_seconds = retry_seconds
        self.retention_days = retention_days
        self.pool: Any = None
        self.lock: Any = None

    async def start(self) -> None:
        self.lock = await asyncpg.connect(self.url, command_timeout=10)
        if not await self.lock.fetchval("SELECT pg_try_advisory_lock(1837192361)"):
            await self.lock.close()
            self.lock = None
            raise RuntimeError("A gateway already owns this database; use one backend worker")
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=4, command_timeout=10)
        await self.pool.execute(DDL)
        await self.pool.execute(
            "UPDATE llm_browser_gateway.attempts SET state='unknown', updated_at=now() "
            "WHERE state IN ('reserved','submitted')"
        )
        await self.pool.execute(
            "INSERT INTO llm_browser_gateway.availability(profile_id,provider,reason) "
            "SELECT DISTINCT profile_id,provider,'submission_unknown' "
            "FROM llm_browser_gateway.attempts WHERE state='unknown' "
            "ON CONFLICT(profile_id,provider) DO UPDATE SET reason='submission_unknown',"
            "reset_at=NULL,next_check_at=NULL,updated_at=now() "
            "WHERE llm_browser_gateway.availability.reason <> 'rate_limited'"
        )
        await self.pool.execute(
            "DELETE FROM llm_browser_gateway.availability WHERE reason = ANY($1::text[])",
            list(REQUEST_FAILURES),
        )
        await self.pool.execute(
            "DELETE FROM llm_browser_gateway.attempts WHERE state='finished' "
            "AND updated_at < now() - $1::int * interval '1 day'",
            self.retention_days,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
        if self.lock is not None:
            await self.lock.close()

    async def availability(self, profile: str, provider: str) -> Availability | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM llm_browser_gateway.availability WHERE profile_id=$1 AND provider=$2",
            profile,
            provider,
        )
        return Availability(row["reason"], row["reset_at"], row["next_check_at"]) if row else None

    async def reserve(
        self, profile: str, provider: str, attempt: str, request: str, slot: int = 0
    ) -> bool:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))", profile, provider
            )
            row = await conn.fetchval(
                "INSERT INTO llm_browser_gateway.attempts "
                "(attempt_id,request_id,profile_id,provider,slot,state) "
                "SELECT $1,$2,$3,$4,$5,'reserved' WHERE NOT EXISTS "
                "(SELECT 1 FROM llm_browser_gateway.availability WHERE profile_id=$3 "
                "AND provider=$4 AND (next_check_at IS NULL OR next_check_at>now())) "
                "AND NOT EXISTS (SELECT 1 FROM llm_browser_gateway.attempts "
                "WHERE profile_id=$3 AND provider=$4 AND state='unknown') "
                "ON CONFLICT DO NOTHING RETURNING attempt_id",
                attempt,
                request,
                profile,
                provider,
                slot,
            )
        return row is not None

    async def submitted(self, attempt: str) -> None:
        await self.pool.execute(
            "UPDATE llm_browser_gateway.attempts SET state='submitted',updated_at=now() "
            "WHERE attempt_id=$1 AND state='reserved'",
            attempt.removesuffix("-repair"),
        )

    async def finish(self, profile: str, provider: str, attempt: str, result: Result) -> None:
        unknown = result.status == Status.UNKNOWN
        now = datetime.now(UTC)
        next_check = result.reset_at or now + timedelta(seconds=self.retry_seconds)
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))", profile, provider
            )
            await conn.execute(
                "UPDATE llm_browser_gateway.attempts SET state=$2,outcome=$3,updated_at=now() "
                "WHERE attempt_id=$1",
                attempt,
                "unknown" if unknown else "finished",
                result.status,
            )
            # Schema/format failures belong to this request, not the provider's availability.
            if result.status in REQUEST_FAILURES:
                return
            if result.status in {Status.SUCCESS, Status.REFUSED}:
                await conn.execute(
                    "DELETE FROM llm_browser_gateway.availability "
                    "WHERE profile_id=$1 AND provider=$2 AND updated_at <= "
                    "(SELECT created_at FROM llm_browser_gateway.attempts WHERE attempt_id=$3)",
                    profile,
                    provider,
                    attempt,
                )
            else:
                await conn.execute(
                    "INSERT INTO llm_browser_gateway.availability "
                    "(profile_id,provider,reason,reset_at,next_check_at) VALUES($1,$2,$3,$4,$5) "
                    "ON CONFLICT(profile_id,provider) DO UPDATE SET reason=$3,reset_at=$4,"
                    "next_check_at=$5,updated_at=now() WHERE "
                    "llm_browser_gateway.availability.reason NOT IN "
                    "('rate_limited','submission_unknown') OR "
                    "llm_browser_gateway.availability.next_check_at<=now() OR "
                    "EXCLUDED.reason='rate_limited'",
                    profile,
                    provider,
                    result.status,
                    result.reset_at,
                    None if unknown else next_check,
                )

    async def terminal(self, profile: str, provider: str, attempt: str) -> None:
        # Late results release only unknown attempts; the engine owns normal active attempts.
        async with self.pool.acquire() as conn, conn.transaction():
            changed = await conn.fetchval(
                "UPDATE llm_browser_gateway.attempts SET state='finished',outcome='reconciled',"
                "updated_at=now() WHERE attempt_id=$1 AND profile_id=$2 AND provider=$3 "
                "AND state='unknown' RETURNING attempt_id",
                attempt.removesuffix("-repair"),
                profile,
                provider,
            )
            if changed:
                await conn.execute(
                    "DELETE FROM llm_browser_gateway.availability WHERE profile_id=$1 "
                    "AND provider=$2 AND reason='submission_unknown' AND NOT EXISTS "
                    "(SELECT 1 FROM llm_browser_gateway.attempts WHERE profile_id=$1 "
                    "AND provider=$2 AND state='unknown')",
                    profile,
                    provider,
                )

    async def reset(self, profile: str) -> None:
        await self.pool.execute(
            "UPDATE llm_browser_gateway.attempts SET state='finished',outcome='manual_reset',"
            "updated_at=now() WHERE profile_id=$1 AND state='unknown'",
            profile,
        )
        await self.pool.execute(
            "DELETE FROM llm_browser_gateway.availability WHERE profile_id=$1",
            profile,
        )

    async def snapshot(self, profile: str) -> dict[str, Any]:
        rows = await self.pool.fetch(
            "SELECT provider,reason,reset_at,next_check_at FROM llm_browser_gateway.availability "
            "WHERE profile_id=$1",
            profile,
        )
        blocked = await self.pool.fetch(
            "SELECT provider,slot,request_id,attempt_id,state FROM llm_browser_gateway.attempts "
            "WHERE profile_id=$1 AND state IN ('reserved','submitted','unknown')",
            profile,
        )
        return {
            "availability": [dict(row) for row in rows],
            "active": [dict(row) for row in blocked],
        }
