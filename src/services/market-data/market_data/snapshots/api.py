"""Read-only snapshot endpoints; existing price API response shapes stay untouched."""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime
from shared.schemas.messages import AssetId, IntradayBar, PriceSampleObserved

from market_data.avanza import AvanzaPending, AvanzaReader, AvanzaUnavailable, SessionSummary
from market_data.snapshots.config import SnapshotSettings
from market_data.snapshots.storage import status

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


def _reader(request: Request) -> AvanzaReader:
    ctx = request.app.state.ctx
    settings = SnapshotSettings(
        database_url=ctx.settings.database_url, rabbitmq_url=ctx.settings.rabbitmq_url
    )
    return AvanzaReader(ctx.pool, settings.mappings_path)


@router.get("/ohlc")
async def session_ohlc(request: Request, asset_id: AssetId, session: date) -> SessionSummary:
    """Observed session OHLC, including provisional state and explicit coverage gaps."""
    try:
        return await _reader(request).summary(asset_id, session, datetime.now(UTC))
    except AvanzaPending as exc:
        raise HTTPException(409, str(exc)) from None
    except AvanzaUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except Exception:
        raise HTTPException(503, "AVANZA_READ_UNAVAILABLE") from None


@router.get("/minutes")
async def sampled_minutes(
    request: Request, asset_id: AssetId, session: date,
) -> list[IntradayBar]:
    """Completed last-known-price minutes; each row retains its original observation."""
    try:
        return await _reader(request).minutes(asset_id, session, datetime.now(UTC))
    except AvanzaPending as exc:
        raise HTTPException(409, str(exc)) from None
    except AvanzaUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except Exception:
        raise HTTPException(503, "AVANZA_READ_UNAVAILABLE") from None


@router.get("/status")
async def snapshot_status(request: Request) -> dict[str, object]:
    return await status(request.app.state.ctx.pool)


@router.get("/recent")
async def recent_samples(
    request: Request,
    asset_id: AssetId,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    before: AwareDatetime | None = None,
) -> list[PriceSampleObserved]:
    pool = request.app.state.ctx.pool
    if not await pool.fetchval("SELECT to_regclass('market_data.price_samples')"):
        return []
    rows = await pool.fetch(
        "SELECT payload FROM market_data.price_samples WHERE asset_id=$1 "
        "AND ($3::timestamptz IS NULL OR observed_at<$3) "
        "ORDER BY observed_at DESC,sample_id DESC LIMIT $2",
        str(asset_id),
        limit,
        before,
    )
    return [PriceSampleObserved.model_validate_json(row["payload"]) for row in rows]
