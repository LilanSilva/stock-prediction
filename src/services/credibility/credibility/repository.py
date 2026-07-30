"""PostgreSQL repository for the ``credibility`` schema.

Owns the idempotency guard, the current-state upsert, and the append-only history log. A single
``PredictionScored`` is applied inside one transaction so the guard row, the source-state upserts,
and every history row (for both edges and sources) commit together or not at all.
"""

from __future__ import annotations

import uuid

import asyncpg

from credibility.history import beta_credible_interval
from credibility.updater import WeightUpdate


class CredibilityRepository:
    """Owns all reads/writes for the ``credibility`` schema."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def already_processed(self, prediction_id: uuid.UUID) -> bool:
        """True when this prediction's credit has already been applied (redelivery no-op)."""
        row = await self._pool.fetchval(
            "SELECT 1 FROM credibility.processed_predictions WHERE prediction_id = $1",
            prediction_id,
        )
        return row is not None

    async def get_source_state(self, source_id: str) -> tuple[float, float] | None:
        """Return the stored ``(alpha, beta)`` for a source, or None if it has never been seen."""
        row = await self._pool.fetchrow(
            "SELECT alpha, beta FROM credibility.credibility "
            "WHERE entity_id = $1 AND entity_type = 'source'",
            source_id,
        )
        if row is None:
            return None
        return float(row["alpha"]), float(row["beta"])

    async def commit_updates(
        self, prediction_id: uuid.UUID, updates: list[WeightUpdate]
    ) -> bool:
        """Persist all updates for one prediction atomically; returns False if already applied.

        Within one transaction: insert the idempotency guard row (skip everything if it already
        exists), upsert current state for every ``source`` update (edge state lives in Neo4j, not
        here), and append one history row per update (edges included) with its 95% CI.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                claimed = await conn.fetchval(
                    """
                    INSERT INTO credibility.processed_predictions (prediction_id)
                    VALUES ($1)
                    ON CONFLICT (prediction_id) DO NOTHING
                    RETURNING prediction_id
                    """,
                    prediction_id,
                )
                if claimed is None:
                    return False

                for update in updates:
                    if update.entity_type == "source":
                        await conn.execute(
                            """
                            INSERT INTO credibility.credibility
                                (entity_id, entity_type, alpha, beta, credibility_score,
                                 last_updated)
                            VALUES ($1, 'source', $2, $3, $4, now())
                            ON CONFLICT (entity_id, entity_type)
                            DO UPDATE SET alpha = EXCLUDED.alpha, beta = EXCLUDED.beta,
                                          credibility_score = EXCLUDED.credibility_score,
                                          last_updated = now()
                            """,
                            update.entity_id,
                            update.alpha_after,
                            update.beta_after,
                            update.credibility_after,
                        )

                    ci_lower, ci_upper = beta_credible_interval(
                        update.alpha_after, update.beta_after
                    )
                    await conn.execute(
                        """
                        INSERT INTO credibility.credibility_history
                            (entity_id, entity_type, prediction_id, alpha_before, beta_before,
                             alpha_after, beta_after, credibility_before, credibility_after,
                             ci_lower, ci_upper)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        """,
                        update.entity_id,
                        update.entity_type,
                        prediction_id,
                        update.alpha_before,
                        update.beta_before,
                        update.alpha_after,
                        update.beta_after,
                        update.credibility_before,
                        update.credibility_after,
                        ci_lower,
                        ci_upper,
                    )
                return True
