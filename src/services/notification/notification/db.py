"""Read-only Postgres helpers for the Notification Service.

Fetches headlines from cleansing.cluster_articles using the event_ids carried
in PredictionMade. Headlines are best-effort: callers must handle an empty list.
"""

from __future__ import annotations

import uuid

import asyncpg


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 3) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def fetch_headlines(
    pool: asyncpg.Pool,
    event_ids: list[uuid.UUID],
    *,
    limit: int = 3,
) -> list[tuple[str, str]]:
    """Return up to ``limit`` (title, source_id) pairs for the given event_ids."""
    if not event_ids:
        return []
    rows = await pool.fetch(
        """
        SELECT ca.title, ca.source_id
        FROM cleansing.events e
        JOIN cleansing.cluster_articles ca ON ca.cluster_id = e.cluster_id
        WHERE e.event_id = ANY($1)
        ORDER BY ca.published_at DESC
        LIMIT $2
        """,
        event_ids,
        limit,
    )
    return [(row["title"], row["source_id"]) for row in rows]
