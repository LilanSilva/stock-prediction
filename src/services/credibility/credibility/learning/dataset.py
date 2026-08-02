"""Read-only dataset builder for the offline structure learner.

Cross-schema READ exception: this offline analytics batch reads ``cleansing.*`` and
``market_data.*`` directly (it does not own them). That is acceptable for a deterministic batch job
and never mutates those schemas; it only writes back to Neo4j via the seed writer.

For each historical event it resolves the baseline/settlement sessions from the event's decision
time, looks up the asset's two closes, and emits one :class:`Sample` per affected asset per
condition (an unconditional ``condition=None`` sample plus one per ``context_tag``). Events with
missing price data on either session are skipped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import asyncpg
import structlog
from pydantic import ValidationError
from shared.calendar import NEW_YORK, resolve_baseline_settlement
from shared.schemas.messages import AssetId, ConditionCode, EventDetected

from credibility.learning.models import Sample

logger = structlog.get_logger(__name__)

_EVENTS_QUERY = """
SELECT event_id, payload
FROM cleansing.events
WHERE (payload->>'first_seen_at')::timestamptz >= $1
ORDER BY (payload->>'first_seen_at')::timestamptz
"""

_CLOSE_QUERY = """
SELECT close
FROM market_data.close_observations
WHERE asset_id = $1 AND session = $2
ORDER BY registry_version DESC
LIMIT 1
"""


async def _load_close(
    conn: asyncpg.Connection,
    cache: dict[tuple[str, date], float | None],
    asset: AssetId,
    session: date,
) -> float | None:
    """Fetch (memoised) the close for one asset/session, or None when no bar was observed."""
    key = (asset.value, session)
    if key not in cache:
        value = await conn.fetchval(_CLOSE_QUERY, asset.value, session)
        cache[key] = None if value is None else float(value)
    return cache[key]


def _conditions(event: EventDetected) -> list[ConditionCode | None]:
    """Unconditional observation plus one per context tag (deduplicated, order preserved)."""
    conditions: list[ConditionCode | None] = [None]
    for tag in event.context_tags:
        if tag not in conditions:
            conditions.append(tag)
    return conditions


async def build_samples(
    pool: asyncpg.Pool, *, lookback_days: int, timezone_name: str = NEW_YORK
) -> list[Sample]:
    """Build the realised sample set from the last ``lookback_days`` of events."""
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    samples: list[Sample] = []
    close_cache: dict[tuple[str, date], float | None] = {}
    skipped_missing_price = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(_EVENTS_QUERY, cutoff)
        for row in rows:
            raw_payload = row["payload"]
            try:
                event = EventDetected.model_validate_json(raw_payload)
            except ValidationError:
                logger.warning("learning_skip_unparseable_event", event_id=str(row["event_id"]))
                continue

            baseline, settlement = resolve_baseline_settlement(event.first_seen_at, timezone_name)
            for asset in event.affected_asset_ids:
                baseline_close = await _load_close(conn, close_cache, asset, baseline)
                settlement_close = await _load_close(conn, close_cache, asset, settlement)
                if baseline_close is None or settlement_close is None:
                    skipped_missing_price += 1
                    continue

                actual_return = (settlement_close - baseline_close) / baseline_close
                for condition in _conditions(event):
                    samples.append(
                        Sample(
                            factor=event.event_type,
                            condition=condition,
                            polarity=event.polarity,
                            asset=asset,
                            actual_return=actual_return,
                        )
                    )

    logger.info(
        "learning_samples_built",
        events=len(rows),
        samples=len(samples),
        skipped_missing_price=skipped_missing_price,
    )
    return samples
