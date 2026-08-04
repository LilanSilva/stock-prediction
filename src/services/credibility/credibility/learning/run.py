"""CLI entrypoint for the offline structure learner: ``python -m credibility.learning.run``.

Wires the Postgres pool and the shared Neo4j client from existing config helpers, runs
build -> estimate -> write once, logs a summary, and tears everything down. Deterministic and
statistics-only: no LLM, no message bus, no long-running loop.
"""

from __future__ import annotations

import asyncio

import asyncpg
import structlog
from shared.graph import CausalGraphClient, Neo4jSettings
from shared.logging import setup_logging

from credibility.db import create_pool
from credibility.learning.config import LearningSettings
from credibility.learning.dataset import build_samples
from credibility.learning.estimator import estimate_edges
from credibility.learning.seed_writer import write_estimates

logger = structlog.get_logger(__name__)


async def run_with(
    pool: asyncpg.Pool, graph: CausalGraphClient, settings: LearningSettings
) -> int:
    """Run one build -> estimate -> write pass on an existing pool + graph; return edges written."""
    samples = await build_samples(
        pool,
        lookback_days=settings.lookback_days,
        volatility_lookback_days=settings.volatility_lookback_days,
        abnormal_threshold=settings.abnormal_threshold,
    )
    estimates = estimate_edges(
        samples, deadband=settings.deadband, min_samples=settings.min_samples
    )
    written = await write_estimates(graph, estimates)
    logger.info(
        "learning_run_complete",
        samples=len(samples),
        estimates=len(estimates),
        edges_written=written,
        lookback_days=settings.lookback_days,
        deadband=settings.deadband,
        min_samples=settings.min_samples,
    )
    return written


async def run(settings: LearningSettings | None = None) -> int:
    """Run one learning pass with its own pool + graph (CLI path); return edges written."""
    settings = settings or LearningSettings()
    setup_logging("credibility.learning", settings.log_level)

    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        written = await run_with(pool, graph, settings)
    finally:
        await graph.close()
        await pool.close()
    return written


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
