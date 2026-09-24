"""PostgreSQL/pgvector persistence for the Cleansing pipeline.

All idempotency and dual-gate state lives here. Writes are keyed by `article_id` so replaying an
`ArticleIngested` message never creates duplicate fingerprints, embeddings, actions, cluster
memberships, or events (acceptance criterion 1). Event persistence and its outbox row are written in
one transaction (functional document sec 3 step 12).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import asyncpg
from shared.schemas.messages import EventDetected, EventType, RoutingKey

from cleansing.clustering import ClusterCandidate, update_centroid
from cleansing.config import CleansingSettings
from cleansing.db import parse_vector, vector_literal
from cleansing.models import ArticleFacts, ClusterRecord, ClusterState, ExtractedAction


class CleansingRepository:
    """Owns all reads/writes against the `cleansing` schema."""

    def __init__(self, pool: asyncpg.Pool, *, processing_version: str | None = None) -> None:
        self._pool = pool
        self._processing_version = processing_version or CleansingSettings().processing_version

    async def article_already_processed(self, article_id: uuid.UUID) -> bool:
        """True when the article already has a fingerprint (idempotent replay guard)."""
        row = await self._pool.fetchval(
            "SELECT 1 FROM cleansing.article_fingerprints WHERE article_id = $1",
            article_id,
        )
        return row is not None

    async def recent_fingerprints(self, since: datetime) -> list[int]:
        """Return SimHash fingerprints seen since `since` (the rolling dedup window)."""
        rows = await self._pool.fetch(
            "SELECT simhash FROM cleansing.article_fingerprints "
            "WHERE published_at >= $1 AND processing_version = $2",
            since,
            self._processing_version,
        )
        return [int(row["simhash"]) for row in rows]

    async def store_fingerprint(
        self,
        article_id: uuid.UUID,
        simhash: int,
        source_id: str,
        published_at: datetime,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO cleansing.article_fingerprints
                (article_id, simhash, source_id, published_at, processing_version)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (article_id) DO NOTHING
            """,
            article_id,
            str(simhash),
            source_id,
            published_at,
            self._processing_version,
        )

    async def store_embedding(self, article_id: uuid.UUID, vector: list[float]) -> None:
        await self._pool.execute(
            """
            INSERT INTO cleansing.article_embeddings (article_id, embedding, processing_version)
            VALUES ($1, $2::vector, $3)
            ON CONFLICT (article_id) DO NOTHING
            """,
            article_id,
            vector_literal(vector),
            self._processing_version,
        )

    async def store_action(
        self, article_id: uuid.UUID, action: ExtractedAction, language: str
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO cleansing.article_actions
                (article_id, actor, action_lemma, object, original_lemma,
                 event_type, language, affected_asset_ids, polarity, context_tags,
                 classification_audit)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            ON CONFLICT (article_id) DO NOTHING
            """,
            article_id,
            action.actor,
            action.action_lemma,
            action.object,
            action.original_lemma,
            action.event_type.value,
            language,
            [asset.value for asset in action.affected_asset_ids],
            action.polarity.value,
            [tag.value for tag in action.context_tags],
            json.dumps(action.classification_audit),
        )

    async def find_candidate_clusters(
        self,
        vector: list[float],
        event_type: EventType,
        *,
        limit: int = 5,
    ) -> list[ClusterCandidate]:
        """Nearest OPEN/QUIET clusters of the same canonical type, by cosine similarity.

        Gate 2 (type compatibility) is applied in SQL; Gate 1 (threshold) is applied by the caller
        so the exact cutoff stays in one place.
        """
        rows = await self._pool.fetch(
            """
            SELECT cluster_id, event_type,
                   1 - (centroid <=> $1::vector) AS similarity
            FROM cleansing.event_clusters
            WHERE state IN ('OPEN', 'QUIET') AND event_type = $2 AND processing_version = $4
            ORDER BY centroid <=> $1::vector
            LIMIT $3
            """,
            vector_literal(vector),
            event_type.value,
            limit,
            self._processing_version,
        )
        return [
            ClusterCandidate(
                cluster_id=row["cluster_id"],
                event_type=EventType(row["event_type"]),
                similarity=float(row["similarity"]),
            )
            for row in rows
        ]

    async def create_cluster(
        self,
        facts: ArticleFacts,
        event_type: EventType,
        vector: list[float],
        *,
        quiet_deadline: datetime,
        lifetime_deadline: datetime,
    ) -> uuid.UUID:
        """Open a new cluster seeded with one article (transactionally adds membership)."""
        cluster_id = uuid.uuid4()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO cleansing.event_clusters
                        (cluster_id, event_type, state, article_count, first_seen_at,
                         last_seen_at, quiet_deadline, lifetime_deadline, centroid,
                         processing_version)
                    VALUES ($1, $2, 'OPEN', 1, $3, $3, $4, $5, $6::vector, $7)
                    """,
                    cluster_id,
                    event_type.value,
                    facts.published_at,
                    quiet_deadline,
                    lifetime_deadline,
                    vector_literal(vector),
                    self._processing_version,
                )
                await self._insert_membership(conn, cluster_id, facts)
        return cluster_id

    async def add_article_to_cluster(
        self,
        cluster_id: uuid.UUID,
        facts: ArticleFacts,
        article_vector: list[float],
        *,
        quiet_deadline: datetime,
    ) -> None:
        """Add an article to an OPEN/QUIET cluster, resetting the quiet timer (transactional).

        The centroid is updated as an incremental running mean under a row lock, so the current
        count and centroid are read and written atomically.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT article_count, centroid::text AS centroid
                    FROM cleansing.event_clusters
                    WHERE cluster_id = $1
                    FOR UPDATE
                    """,
                    cluster_id,
                )
                if row is None:
                    return
                count = int(row["article_count"])
                current = parse_vector(row["centroid"])
                new_centroid = update_centroid(current, count, article_vector)
                await conn.execute(
                    """
                    UPDATE cleansing.event_clusters
                    SET article_count = article_count + 1,
                        last_seen_at = $2,
                        quiet_deadline = $3,
                        centroid = $4::vector,
                        state = 'OPEN',
                        updated_at = now()
                    WHERE cluster_id = $1
                    """,
                    cluster_id,
                    facts.published_at,
                    quiet_deadline,
                    vector_literal(new_centroid),
                )
                await self._insert_membership(conn, cluster_id, facts)

    @staticmethod
    async def _insert_membership(
        conn: asyncpg.Connection, cluster_id: uuid.UUID, facts: ArticleFacts
    ) -> None:
        await conn.execute(
            """
            INSERT INTO cleansing.cluster_articles
                (cluster_id, article_id, title, source_id, canonical_url,
                 published_at, correlation_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (cluster_id, article_id) DO NOTHING
            """,
            cluster_id,
            facts.article_id,
            facts.title,
            facts.source_id,
            facts.canonical_url,
            facts.published_at,
            facts.correlation_id,
        )

    async def claim_ready_clusters(self, now: datetime, *, limit: int = 20) -> list[ClusterRecord]:
        """Atomically transition due OPEN/QUIET clusters to MERGING and return them.

        Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent close sweeps never double-process a
        cluster. Readiness is time-based only (quiet period elapsed or max-lifetime watermark);
        article count is never a trigger.
        """
        claimed: list[ClusterRecord] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT cluster_id, event_type, state, article_count, first_seen_at,
                           last_seen_at, quiet_deadline, lifetime_deadline,
                           centroid::text AS centroid
                    FROM cleansing.event_clusters
                    WHERE state IN ('OPEN', 'QUIET')
                      AND (quiet_deadline <= $1 OR lifetime_deadline <= $1)
                    ORDER BY quiet_deadline
                    LIMIT $2
                    FOR UPDATE SKIP LOCKED
                    """,
                    now,
                    limit,
                )
                for row in rows:
                    await conn.execute(
                        """
                        UPDATE cleansing.event_clusters
                        SET state = 'MERGING', updated_at = now()
                        WHERE cluster_id = $1
                        """,
                        row["cluster_id"],
                    )
                    claimed.append(
                        ClusterRecord(
                            cluster_id=row["cluster_id"],
                            event_type=EventType(row["event_type"]),
                            state=ClusterState.MERGING,
                            article_count=row["article_count"],
                            first_seen_at=row["first_seen_at"],
                            last_seen_at=row["last_seen_at"],
                            quiet_deadline=row["quiet_deadline"],
                            lifetime_deadline=row["lifetime_deadline"],
                            centroid=parse_vector(row["centroid"]),
                        )
                    )
        return claimed

    async def load_cluster_articles(self, cluster_id: uuid.UUID) -> list[asyncpg.Record]:
        """Return membership rows (title/source/url/published_at) for event construction."""
        return list(
            await self._pool.fetch(
                """
                SELECT article_id, title, source_id, canonical_url, published_at, correlation_id
                FROM cleansing.cluster_articles
                WHERE cluster_id = $1
                ORDER BY published_at
                """,
                cluster_id,
            )
        )

    async def load_cluster_actions(self, cluster_id: uuid.UUID) -> list[asyncpg.Record]:
        """Return per-article extracted actions for the cluster (ambiguity/conflict detection)."""
        return list(
            await self._pool.fetch(
                """
                SELECT a.article_id, a.actor, a.action_lemma, a.object, a.event_type,
                       a.affected_asset_ids, a.polarity, a.context_tags
                FROM cleansing.article_actions a
                JOIN cleansing.cluster_articles ca ON ca.article_id = a.article_id
                WHERE ca.cluster_id = $1
                """,
                cluster_id,
            )
        )

    async def set_cluster_state(self, cluster_id: uuid.UUID, state: ClusterState) -> None:
        await self._pool.execute(
            """
            UPDATE cleansing.event_clusters
            SET state = $2, updated_at = now()
            WHERE cluster_id = $1
            """,
            cluster_id,
            state.value,
        )

    async def store_event_with_outbox(self, event: EventDetected) -> None:
        """Persist the event and its outbox row and mark the cluster MERGED in one transaction."""
        payload = event.model_dump_json()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO cleansing.events
                        (event_id, cluster_id, event_type, canonical_summary,
                         extraction_method, payload)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                    ON CONFLICT (cluster_id) DO NOTHING
                    """,
                    event.event_id,
                    event.cluster_id,
                    event.event_type.value,
                    event.canonical_summary,
                    event.extraction_method.value,
                    payload,
                )
                await conn.execute(
                    """
                    INSERT INTO cleansing.outbox_events
                        (message_id, aggregate_id, routing_key, payload)
                    VALUES ($1, $2, $3, $4::jsonb)
                    ON CONFLICT (message_id) DO NOTHING
                    """,
                    event.message_id,
                    event.event_id,
                    RoutingKey.EVENT_DETECTED.value,
                    payload,
                )
                await conn.execute(
                    """
                    UPDATE cleansing.event_clusters
                    SET state = 'MERGED', updated_at = now()
                    WHERE cluster_id = $1
                    """,
                    event.cluster_id,
                )
