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
from shared.schemas.messages import AssetId, ConditionCode, Direction, EventType

logger = structlog.get_logger(__name__)

# Condition is a property on the CAUSES edge, so each (factor, asset, condition) is a distinct edge
# with its own weight/reliability. Unconditional edges have no `condition` property and always fire;
# conditioned edges fire only when their condition is in the active set (null filter = any).
_FIRING_EDGES_CYPHER = """
MATCH (cf:CausalFactor {id: $event_type})-[r:CAUSES]->(a:Asset)
WHERE ($asset_ids IS NULL OR a.id IN $asset_ids)
  AND (r.condition IS NULL OR $conditions IS NULL OR r.condition IN $conditions)
RETURN cf.id AS factor_id, a.id AS asset_id, r.direction AS direction,
       r.weight AS weight, r.confidence AS confidence, r.alpha AS alpha, r.beta AS beta,
       r.condition AS condition
"""

_UPDATE_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES]->(a:Asset {id: $asset_id})
WHERE r.condition IS NULL
SET r.alpha = $alpha, r.beta = $beta, r.last_updated = datetime()
RETURN r.alpha AS alpha, r.beta AS beta
"""

_UPDATE_CONDITIONED_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES {condition: $condition}]->
      (a:Asset {id: $asset_id})
SET r.alpha = $alpha, r.beta = $beta, r.last_updated = datetime()
RETURN r.alpha AS alpha, r.beta AS beta
"""

_UPSERT_CONDITIONED_EDGE_CYPHER = """
MERGE (cf:CausalFactor {id: $factor_id})
MERGE (a:Asset {id: $asset_id})
MERGE (cf)-[r:CAUSES {condition: $condition}]->(a)
SET r.direction = $direction, r.weight = $weight, r.confidence = $confidence,
    r.alpha = $alpha, r.beta = $beta, r.last_updated = datetime()
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
        self,
        event_type: EventType,
        asset_ids: list[AssetId] | None = None,
        conditions: set[ConditionCode] | None = None,
    ) -> list[FiringEdge]:
        """Return edges for ``event_type`` gated by ``asset_ids`` and active ``conditions``.

        Unconditional edges always fire; conditioned edges fire only when their condition is in
        ``conditions`` (``None`` means no condition filter — return every conditioned edge).
        """
        driver = self._require_driver()
        params = {
            "event_type": event_type.value,
            "asset_ids": [a.value for a in asset_ids] if asset_ids is not None else None,
            "conditions": [c.value for c in conditions] if conditions is not None else None,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_FIRING_EDGES_CYPHER, params)
                records = await result.data()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j firing-edge query failed: {exc}") from exc

        edges: list[FiringEdge] = []
        for row in cast(list[dict[str, Any]], records):
            raw_condition = row.get("condition")
            edges.append(
                FiringEdge(
                    factor_id=EventType(row["factor_id"]),
                    asset_id=AssetId(row["asset_id"]),
                    direction=Direction(row["direction"]),
                    weight=float(row["weight"]),
                    confidence=float(row["confidence"]),
                    alpha=float(row["alpha"]),
                    beta=float(row["beta"]),
                    condition=ConditionCode(raw_condition) if raw_condition is not None else None,
                )
            )
        return edges

    async def update_edge_weight(
        self,
        factor_id: EventType,
        asset_id: AssetId,
        *,
        alpha: float,
        beta: float,
        condition: ConditionCode | None = None,
    ) -> None:
        """Persist Beta-Bernoulli counts for a CAUSES edge (used by Credibility).

        When ``condition`` is given the conditioned edge is updated; otherwise the legacy
        unconditional edge is updated (keeps older scored messages working).
        """
        driver = self._require_driver()
        if condition is None:
            cypher = _UPDATE_EDGE_CYPHER
            params: dict[str, Any] = {
                "factor_id": factor_id.value,
                "asset_id": asset_id.value,
                "alpha": alpha,
                "beta": beta,
            }
            edge_label = f"{factor_id.value}->{asset_id.value}"
        else:
            cypher = _UPDATE_CONDITIONED_EDGE_CYPHER
            params = {
                "factor_id": factor_id.value,
                "asset_id": asset_id.value,
                "condition": condition.value,
                "alpha": alpha,
                "beta": beta,
            }
            edge_label = f"{factor_id.value}|{condition.value}->{asset_id.value}"
        try:
            async with driver.session() as session:
                result = await session.run(cypher, params)
                updated = await result.single()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j edge-weight update failed: {exc}") from exc
        if updated is None:
            raise GraphTransportError(f"no CAUSES edge for {edge_label}")

    async def upsert_conditioned_edge(
        self,
        factor_id: EventType,
        condition: ConditionCode,
        asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> None:
        """Create or refine a conditioned edge (used by the offline structure learner).

        Idempotent MERGE: keeps expert-seeded edges as the prior and overwrites their statistics
        with data-derived values. Creates the factor/condition/asset nodes and ``UNDER`` link if
        absent.
        """
        driver = self._require_driver()
        params = {
            "factor_id": factor_id.value,
            "condition": condition.value,
            "asset_id": asset_id.value,
            "direction": direction.value,
            "weight": weight,
            "confidence": confidence,
            "alpha": alpha,
            "beta": beta,
        }
        try:
            async with driver.session() as session:
                await session.run(_UPSERT_CONDITIONED_EDGE_CYPHER, params)
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j conditioned-edge upsert failed: {exc}") from exc
