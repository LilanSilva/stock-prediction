"""Time-based retention cleanup for the ingestion schema.

`ingestion.articles` is the de-duplication anchor, but RSS/GDELT only ever surface *recent* items,
so an article older than any feed's lookback window can never be re-ingested and is safe to delete.
Delivered outbox rows are safe to prune once they have been delivered. This keeps table growth
bounded while preserving the de-duplication guarantee for the (short) feed window.
"""

from __future__ import annotations

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


async def _delete(conn: asyncpg.Connection, sql: str, *args: object) -> int:
    status = await conn.execute(sql, *args)
    # asyncpg returns a command tag like "DELETE 5"; the trailing token is the row count.
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0


class RetentionCleaner:
    """Deletes aged article rows and delivered outbox rows on demand."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        article_retention_days: int = 30,
        outbox_retention_days: int = 7,
    ) -> None:
        self._pool = pool
        self._article_retention_days = article_retention_days
        self._outbox_retention_days = outbox_retention_days

    async def run(self) -> tuple[int, int]:
        """Delete aged rows; returns (articles_deleted, delivered_outbox_deleted)."""
        async with self._pool.acquire() as conn:
            articles_deleted = await _delete(
                conn,
                """
                DELETE FROM ingestion.articles
                WHERE ingested_at < now() - make_interval(days => $1)
                """,
                self._article_retention_days,
            )
            outbox_deleted = await _delete(
                conn,
                """
                DELETE FROM ingestion.outbox
                WHERE delivery_status = 'DELIVERED'
                  AND delivered_at < now() - make_interval(days => $1)
                """,
                self._outbox_retention_days,
            )
        logger.info(
            "retention_cleanup",
            articles_deleted=articles_deleted,
            outbox_deleted=outbox_deleted,
            article_retention_days=self._article_retention_days,
            outbox_retention_days=self._outbox_retention_days,
        )
        return articles_deleted, outbox_deleted
