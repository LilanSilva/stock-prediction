"""Read-only snapshot endpoints; existing price API response shapes stay untouched."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime
from shared.schemas.messages import AssetId, PriceSampleObserved

from market_data.snapshots.storage import status

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


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
