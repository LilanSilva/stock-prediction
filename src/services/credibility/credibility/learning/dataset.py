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

import math
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import asyncpg
import structlog
from pydantic import ValidationError
from shared.calendar import NEW_YORK, resolve_baseline_settlement
from shared.graph import CorrelationEdge
from shared.reference import UnknownAssetError, resolve
from shared.schemas.messages import AssetId, ConditionCode, Direction, EventDetected

from credibility.learning.models import CorrelationSample, Sample

logger = structlog.get_logger(__name__)


class CorrelationEdgeReader(Protocol):
    """The subset of ``shared.graph.CausalGraphClient`` the correlation dataset builder needs."""

    async def get_correlation_edges(
        self, source_asset_id: AssetId, condition: ConditionCode
    ) -> list[CorrelationEdge]: ...

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

_VOLATILITY_QUERY = """
SELECT close
FROM market_data.close_observations
WHERE asset_id = $1 AND session >= $2
ORDER BY session, registry_version DESC
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


def _calculate_volatility(closes: list[float]) -> float:
    """Standard deviation of daily returns from a sequence of closes.

    Returns 0.0 when fewer than two closes are available — the estimator treats this as normal
    (non-abnormal) so those samples still require the full min_samples threshold.
    """
    if len(closes) < 2:
        return 0.0
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


async def _load_volatility(
    conn: asyncpg.Connection,
    cache: dict[str, float],
    asset: AssetId,
    cutoff: date,
) -> float:
    """Fetch (memoised) historical volatility for an asset over the lookback window."""
    key = asset.value
    if key not in cache:
        rows = await conn.fetch(_VOLATILITY_QUERY, asset.value, cutoff)
        closes = [float(row["close"]) for row in rows]
        cache[key] = _calculate_volatility(closes)
    return cache[key]


def _conditions(event: EventDetected) -> list[ConditionCode | None]:
    """Unconditional observation plus one per context tag (deduplicated, order preserved)."""
    conditions: list[ConditionCode | None] = [None]
    for tag in event.context_tags:
        if tag not in conditions:
            conditions.append(tag)
    return conditions


async def build_samples(
    pool: asyncpg.Pool,
    *,
    lookback_days: int,
    timezone_name: str = NEW_YORK,
    volatility_lookback_days: int = 30,
    abnormal_threshold: float = 2.0,
) -> list[Sample]:
    """Build the realised sample set from the last ``lookback_days`` of events.

    Each sample is tagged with ``is_abnormal=True`` when the absolute return exceeds
    ``abnormal_threshold`` multiples of the asset's historical daily volatility computed over
    ``volatility_lookback_days`` of closes. Samples with insufficient volatility history
    (fewer than two closes) are tagged ``is_abnormal=False``.
    """
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    volatility_cutoff = (datetime.now(UTC) - timedelta(days=volatility_lookback_days)).date()
    samples: list[Sample] = []
    close_cache: dict[tuple[str, date], float | None] = {}
    volatility_cache: dict[str, float] = {}
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

            for asset in event.affected_asset_ids:
                # Sessions are resolved per asset, not per event: one event can affect listings on
                # different exchanges, and a Stockholm close is not a New York close.
                try:
                    series = resolve(asset)
                except UnknownAssetError:
                    logger.warning("learning_skip_unknown_asset", asset_id=str(asset))
                    continue
                baseline, settlement = resolve_baseline_settlement(
                    event.first_seen_at,
                    series.timezone,
                    hour=series.session_completion_hour,
                    minute=series.session_completion_minute,
                )
                baseline_close = await _load_close(conn, close_cache, asset, baseline)
                settlement_close = await _load_close(conn, close_cache, asset, settlement)
                if baseline_close is None or settlement_close is None:
                    skipped_missing_price += 1
                    continue

                actual_return = (settlement_close - baseline_close) / baseline_close
                volatility = await _load_volatility(
                    conn, volatility_cache, asset, volatility_cutoff
                )
                is_abnormal = (
                    volatility > 0.0 and abs(actual_return) >= abnormal_threshold * volatility
                )

                for condition in _conditions(event):
                    samples.append(
                        Sample(
                            factor=event.event_type,
                            condition=condition,
                            polarity=event.polarity,
                            asset=asset,
                            actual_return=actual_return,
                            is_abnormal=is_abnormal,
                            asset_volatility=volatility,
                        )
                    )

    logger.info(
        "learning_samples_built",
        events=len(rows),
        samples=len(samples),
        skipped_missing_price=skipped_missing_price,
    )
    return samples


# Direct scored predictions within the lookback window.
# Joins prediction.outbox_events to filter to propagation_depth=0 (direct predictions only).
# Cross-schema read: prediction.*, verification.* — same analytics exception as build_samples.
_DIRECT_SCORED_PREDICTIONS_QUERY = """
SELECT
    p.prediction_id,
    p.asset_id                                          AS source_asset_id,
    p.direction                                         AS predicted_direction,
    e.settlement_session,
    e.baseline_session
FROM prediction.predictions  p
JOIN prediction.outbox_events o ON o.aggregate_id = p.prediction_id
JOIN verification.evaluations e USING (prediction_id)
JOIN verification.scores      s USING (prediction_id)
WHERE p.decision_at >= $1
  AND p.direction  != 'NEUTRAL'
  AND (o.payload::jsonb ->> 'propagation_depth')::int = 0
ORDER BY p.decision_at
"""


async def build_correlation_samples(
    pool: asyncpg.Pool,
    graph: CorrelationEdgeReader,
    *,
    lookback_days: int,
    volatility_lookback_days: int = 30,
    abnormal_threshold: float = 2.0,
) -> list[CorrelationSample]:
    """Build correlation samples from scored direct predictions within lookback_days.

    For each scored direct prediction of source_asset in direction D, queries
    CORRELATES_WITH edges for that asset and condition, then looks up the target
    asset's actual return in the same settlement session. Emits one CorrelationSample
    per (source_asset, condition, target_asset) observation.
    """
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    volatility_cutoff = (datetime.now(UTC) - timedelta(days=volatility_lookback_days)).date()
    samples: list[CorrelationSample] = []
    close_cache: dict[tuple[str, date], float | None] = {}
    volatility_cache: dict[str, float] = {}
    skipped = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(_DIRECT_SCORED_PREDICTIONS_QUERY, cutoff)
        for row in rows:
            # An asset retired from the registry keeps its historical scores; they are not
            # learnable, so skip the row rather than failing the whole batch.
            try:
                source_asset = AssetId(row["source_asset_id"])
            except ValueError:
                logger.warning(
                    "corr_learning_skip_unknown_source_asset",
                    asset_id=row["source_asset_id"],
                )
                continue
            direction = Direction(row["predicted_direction"])
            condition = (
                ConditionCode.UPSTREAM_UP
                if direction is Direction.UP
                else ConditionCode.UPSTREAM_DOWN
            )
            settlement: date = row["settlement_session"]
            baseline: date = row["baseline_session"]

            corr_edges = await graph.get_correlation_edges(source_asset, condition)
            if not corr_edges:
                continue

            for edge in corr_edges:
                target = edge.target_asset_id
                try:
                    resolve(target)
                except UnknownAssetError:
                    logger.warning("corr_learning_skip_unknown_asset", asset_id=str(target))
                    continue

                baseline_close = await _load_close(conn, close_cache, target, baseline)
                settlement_close = await _load_close(conn, close_cache, target, settlement)
                if baseline_close is None or settlement_close is None:
                    skipped += 1
                    continue

                actual_return = (settlement_close - baseline_close) / baseline_close
                volatility = await _load_volatility(
                    conn, volatility_cache, target, volatility_cutoff
                )
                is_abnormal = (
                    volatility > 0.0
                    and abs(actual_return) >= abnormal_threshold * volatility
                )
                samples.append(
                    CorrelationSample(
                        source_asset=source_asset,
                        condition=condition,
                        target_asset=target,
                        actual_return=actual_return,
                        is_abnormal=is_abnormal,
                        asset_volatility=volatility,
                    )
                )

    logger.info(
        "corr_learning_samples_built",
        predictions=len(rows),
        samples=len(samples),
        skipped_missing_price=skipped,
    )
    return samples
