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
from credibility.learning.dataset import build_correlation_samples, build_samples
from credibility.learning.estimator import estimate_correlation_edges, estimate_edges
from credibility.learning.seed_writer import write_correlation_estimates, write_estimates

logger = structlog.get_logger(__name__)


async def run_with(
    pool: asyncpg.Pool, graph: CausalGraphClient, settings: LearningSettings
) -> int:
    """Run one learning pass over both edge types; return the total number of edges written.

    Path 1 covers ``CAUSES`` edges, path 2 covers ``CORRELATES_WITH`` edges.
    """
    # --- Path 1: CAUSES edges (unchanged) ---
    samples = await build_samples(
        pool,
        lookback_days=settings.lookback_days,
        volatility_lookback_days=settings.volatility_lookback_days,
        abnormal_threshold=settings.abnormal_threshold,
    )
    estimates = estimate_edges(
        samples, deadband=settings.deadband, min_samples=settings.min_samples
    )
    written_causes = await write_estimates(graph, estimates)

    # --- Path 2: CORRELATES_WITH edges ---
    written_corr = 0
    if settings.correlation_learning_enabled:
        corr_samples = await build_correlation_samples(
            pool,
            graph,
            lookback_days=settings.lookback_days,
            volatility_lookback_days=settings.volatility_lookback_days,
            abnormal_threshold=settings.abnormal_threshold,
        )
        corr_estimates = estimate_correlation_edges(
            corr_samples, deadband=settings.deadband, min_samples=settings.min_samples
        )
        written_corr = await write_correlation_estimates(graph, corr_estimates)

    total = written_causes + written_corr
    logger.info(
        "learning_run_complete",
        samples=len(samples),
        estimates=len(estimates),
        written_causes=written_causes,
        written_corr=written_corr,
        edges_written=total,
        lookback_days=settings.lookback_days,
        deadband=settings.deadband,
        min_samples=settings.min_samples,
        correlation_enabled=settings.correlation_learning_enabled,
    )
    return total


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
