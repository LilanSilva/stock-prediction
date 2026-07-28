"""Async Neo4j client for the canonical causal knowledge graph.

The ``neo4j`` driver is imported lazily so the base shared package installs without the optional
``graph`` extra. Read access (``get_firing_edges``) is used by Prediction; write access
(``update_edge_weight``) is used by Credibility to persist Beta-Bernoulli updates.
"""

from __future__ import annotations

from typing import Any, cast

import structlog

from shared.graph.exceptions import GraphConfigurationError, GraphTransportError
from shared.graph.models import FiringEdge
from shared.graph.settings import Neo4jSettings
from shared.schemas.messages import AssetId, Direction, EventType

logger = structlog.get_logger(__name__)

# One CAUSES edge per (factor, asset); direction/weight are properties, not separate edges.
_FIRING_EDGES_CYPHER = """
MATCH (cf:CausalFactor {id: $event_type})-[r:CAUSES]->(a:Asset)
WHERE $asset_ids IS NULL OR a.id IN $asset_ids
RETURN cf.id AS factor_id, a.id AS asset_id, r.direction AS direction,
       r.weight AS weight, r.confidence AS confidence, r.alpha AS alpha, r.beta AS beta
"""

_UPDATE_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES]->(a:Asset {id: $asset_id})
SET r.alpha = $alpha, r.beta = $beta, r.last_updated = datetime()
RETURN r.alpha AS alpha, r.beta AS beta
"""


class CausalGraphClient:
    """Async accessor for the canonical ``CausalFactor -[:CAUSES]-> Asset`` graph."""

    def __init__(self, settings: Neo4jSettings) -> None:
        self._settings = settings
        self._driver: Any | None = None

    async def connect(self) -> None:
        """Create the async driver and verify connectivity (fail fast on bad config/auth)."""
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as exc:  # pragma: no cover - only without the `graph` extra
            raise GraphConfigurationError(
                "neo4j not installed; install the 'graph' extra to use CausalGraphClient"
            ) from exc

        driver = AsyncGraphDatabase.driver(
            self._settings.uri,
            auth=(self._settings.user, self._settings.password),
            connection_timeout=self._settings.connection_timeout_seconds,
            max_connection_pool_size=self._settings.max_connection_pool_size,
        )
        try:
            await driver.verify_connectivity()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            await driver.close()
            raise GraphTransportError(f"neo4j connectivity check failed: {exc}") from exc
        self._driver = driver

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    @property
    def is_connected(self) -> bool:
        return self._driver is not None

    async def verify_connectivity(self) -> bool:
        """Return True when the graph answers a connectivity probe (used by ``/ready``)."""
        if self._driver is None:
            return False
        try:
            await self._driver.verify_connectivity()
        except Exception:  # noqa: BLE001 - readiness is a boolean, never raises
            return False
        return True

    def _require_driver(self) -> Any:
        if self._driver is None:
            raise GraphConfigurationError("CausalGraphClient.connect() must be called first")
        return self._driver

    async def get_firing_edges(
        self, event_type: EventType, asset_ids: list[AssetId] | None = None
    ) -> list[FiringEdge]:
        """Return CAUSES edges for ``event_type`` (optionally restricted to ``asset_ids``)."""
        driver = self._require_driver()
        params = {
            "event_type": event_type.value,
            "asset_ids": [a.value for a in asset_ids] if asset_ids is not None else None,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_FIRING_EDGES_CYPHER, params)
                records = await result.data()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j firing-edge query failed: {exc}") from exc

        edges: list[FiringEdge] = []
        for row in cast(list[dict[str, Any]], records):
            edges.append(
                FiringEdge(
                    factor_id=EventType(row["factor_id"]),
                    asset_id=AssetId(row["asset_id"]),
                    direction=Direction(row["direction"]),
                    weight=float(row["weight"]),
                    confidence=float(row["confidence"]),
                    alpha=float(row["alpha"]),
                    beta=float(row["beta"]),
                )
            )
        return edges

    async def update_edge_weight(
        self, factor_id: EventType, asset_id: AssetId, *, alpha: float, beta: float
    ) -> None:
        """Persist Beta-Bernoulli counts for a CAUSES edge (used by Credibility, E07)."""
        driver = self._require_driver()
        params = {
            "factor_id": factor_id.value,
            "asset_id": asset_id.value,
            "alpha": alpha,
            "beta": beta,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_UPDATE_EDGE_CYPHER, params)
                updated = await result.single()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j edge-weight update failed: {exc}") from exc
        if updated is None:
            raise GraphTransportError(
                f"no CAUSES edge for {factor_id.value}->{asset_id.value}"
            )
