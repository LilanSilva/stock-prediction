"""Async Neo4j client for the canonical causal knowledge graph.

The ``neo4j`` driver is imported lazily so the base shared package installs without the optional
``graph`` extra. Read access (``get_firing_edges``) is used by Prediction; write access
(``update_edge_weight``) is used by Credibility to persist outcome-driven edge ``weight`` changes.

Two kinds of write exist and they are deliberately different:

  - ``update_*`` — ``MATCH`` + ``SET``: Credibility revising an existing edge's ``weight`` from a
    scored prediction's outcome.
  - ``upsert_*`` — ``MERGE`` + ``ON CREATE SET``: the offline structure learner *discovering* an
    edge. These never touch an edge that already exists.
"""

from __future__ import annotations

from typing import Any, cast

import structlog

from shared.graph.exceptions import GraphConfigurationError, GraphTransportError
from shared.graph.models import CorrelationEdge, FiringEdge
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

# Industry-level edges, returned against each member asset so the caller sees a normal FiringEdge.
# `inherited` marks the provenance: a newly listed company has no evidence of its own yet, so it
# predicts from its group's edges until the offline learner writes company-specific ones.
#
# The override is by (factor, asset), NOT by (factor, asset, condition): if the asset has ANY of its
# own edges for this factor that is eligible under the active condition set, every group edge for
# that factor is suppressed. Matching on condition equality instead let a learned conditioned asset
# edge sit alongside the unconditional group edge, and BOTH fired — one causal factor counted twice,
# inflating both the confidence mass and the magnitude average. That was live on 2026-08-14: a
# SAFE_HAVEN_ONLY conflict returned two UP edges for NEM_NYSE (its own learned 0.11 plus
# PRECIOUS_METALS' 0.50).
_GROUP_FIRING_EDGES_CYPHER = """
MATCH (cf:CausalFactor {id: $event_type})-[r:CAUSES]->(g:AssetGroup)<-[:MEMBER_OF]-(a:Asset)
WHERE ($asset_ids IS NULL OR a.id IN $asset_ids)
  AND (r.condition IS NULL OR $conditions IS NULL OR r.condition IN $conditions)
  AND NOT EXISTS {
    MATCH (cf)-[own:CAUSES]->(a)
    WHERE own.condition IS NULL OR $conditions IS NULL OR own.condition IN $conditions
  }
RETURN cf.id AS factor_id, a.id AS asset_id, r.direction AS direction,
       r.weight AS weight, r.confidence AS confidence, r.alpha AS alpha, r.beta AS beta,
       r.condition AS condition, g.id AS group_id
"""

# Outcome-driven edge updates move `weight`, not the Beta-Bernoulli counts. `weight` is what
# `decide()` actually consumes for magnitude, whereas `reliability` (alpha/beta) cancels out of the
# net/total ratio whenever a single edge fires — which is 92% of predictions — so counting outcomes
# there had no observable effect. alpha/beta are deliberately left untouched here.
_UPDATE_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES]->(a:Asset {id: $target_id})
WHERE r.condition IS NULL
SET r.weight = $weight, r.last_updated = datetime()
RETURN r.weight AS weight
"""

_UPDATE_CONDITIONED_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES {condition: $condition}]->
      (a:Asset {id: $target_id})
SET r.weight = $weight, r.last_updated = datetime()
RETURN r.weight AS weight
"""

# Industry-level (inherited) edges target an :AssetGroup, not an :Asset. A prediction that fired an
# inherited edge reports the GROUP edge in its `edge_id`, so learning must land on that shared prior
# rather than on a per-asset edge that was never seeded.
_UPDATE_GROUP_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES]->(g:AssetGroup {id: $target_id})
WHERE r.condition IS NULL
SET r.weight = $weight, r.last_updated = datetime()
RETURN r.weight AS weight
"""

_UPDATE_CONDITIONED_GROUP_EDGE_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES {condition: $condition}]->
      (g:AssetGroup {id: $target_id})
SET r.weight = $weight, r.last_updated = datetime()
RETURN r.weight AS weight
"""

# Current counts for one group edge, addressed directly by (factor, group, condition). Reading it
# through a member asset would be wrong: `_GROUP_FIRING_EDGES_CYPHER` hides the group edge from any
# member that has its own edge for that pair, so a member-based lookup can miss it.
_GROUP_EDGE_COUNTS_CYPHER = """
MATCH (cf:CausalFactor {id: $factor_id})-[r:CAUSES]->(g:AssetGroup {id: $group_id})
WHERE ($condition IS NULL AND r.condition IS NULL) OR r.condition = $condition
RETURN r.alpha AS alpha, r.beta AS beta, r.weight AS weight
"""

# ON CREATE SET, never a bare SET: the offline learner may only DISCOVER edges, never revise one
# that already exists. A bare SET let a batch run overwrite an expert-seeded edge — and the online
# outcome-driven weight — from a handful of samples, flipping direction in one case. Existing edges
# are owned by the seed and by Credibility's per-outcome updates; the learner only widens coverage.
_UPSERT_CONDITIONED_EDGE_CYPHER = """
MERGE (cf:CausalFactor {id: $factor_id})
MERGE (a:Asset {id: $asset_id})
MERGE (cf)-[r:CAUSES {condition: $condition}]->(a)
ON CREATE SET r.direction = $direction, r.weight = $weight, r.confidence = $confidence,
    r.alpha = $alpha, r.beta = $beta, r.last_updated = datetime()
RETURN r.alpha AS alpha, r.beta AS beta
"""

_CORRELATION_EDGES_CYPHER = """
MATCH (a1:Asset {id: $source_asset_id})-[r:CORRELATES_WITH]->(a2:Asset)
WHERE r.condition = $condition
RETURN a1.id AS source_asset_id, a2.id AS target_asset_id,
       r.direction AS direction, r.weight AS weight, r.confidence AS confidence,
       r.alpha AS alpha, r.beta AS beta, r.condition AS condition
"""

_UPDATE_CORRELATION_EDGE_CYPHER = """
MATCH (a1:Asset {id: $source_asset_id})-[r:CORRELATES_WITH {condition: $condition}]->
      (a2:Asset {id: $target_asset_id})
SET r.weight = $weight, r.last_updated = datetime()
RETURN r.weight AS weight
"""

_CORRELATION_EDGE_COUNTS_CYPHER = """
MATCH (a1:Asset {id: $source_asset_id})-[r:CORRELATES_WITH {condition: $condition}]->
      (a2:Asset {id: $target_asset_id})
RETURN r.alpha AS alpha, r.beta AS beta, r.weight AS weight
"""

# Add-only, for the same reason as _UPSERT_CONDITIONED_EDGE_CYPHER above: this is the query that
# rewrote the seeded XOM_NYSE->NEM_NYSE edge from DOWN/0.45 to UP/0.107 on ~10 samples.
_UPSERT_CORRELATION_EDGE_CYPHER = """
MERGE (a1:Asset {id: $source_asset_id})
MERGE (a2:Asset {id: $target_asset_id})
MERGE (a1)-[r:CORRELATES_WITH {condition: $condition}]->(a2)
ON CREATE SET r.direction    = $direction,
    r.weight       = $weight,
    r.confidence   = $confidence,
    r.alpha        = $alpha,
    r.beta         = $beta,
    r.last_updated = datetime()
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
        *,
        inherit_group_edges: bool = True,
    ) -> list[FiringEdge]:
        """Return edges for ``event_type`` gated by ``asset_ids`` and active ``conditions``.

        Unconditional edges always fire; conditioned edges fire only when their condition is in
        ``conditions`` (``None`` means no condition filter — return every conditioned edge).

        With ``inherit_group_edges`` (the default), an asset that has no edge of its own for a
        (factor, condition) pair inherits its industry group's edge, flagged ``inherited=True``. A
        company-specific edge always wins over the group's, so evidence learned for that listing
        overrides the industry prior rather than adding to it.
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
                if inherit_group_edges:
                    group_result = await session.run(_GROUP_FIRING_EDGES_CYPHER, params)
                    records = list(records) + list(await group_result.data())
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j firing-edge query failed: {exc}") from exc

        edges: list[FiringEdge] = []
        for row in cast(list[dict[str, Any]], records):
            raw_condition = row.get("condition")
            group_id = row.get("group_id")
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
                    inherited_from=str(group_id) if group_id is not None else None,
                )
            )
        return edges

    async def get_group_edge_counts(
        self,
        factor_id: EventType,
        group_id: str,
        condition: ConditionCode | None = None,
    ) -> tuple[float, float, float] | None:
        """Return ``(alpha, beta, weight)`` for one industry-group edge, or ``None`` if absent.

        ``weight`` is what Credibility adjusts from an outcome; ``alpha``/``beta`` are returned for
        reporting only, since they are no longer moved by outcomes.

        Addressed directly by (factor, group, condition) rather than through a member asset:
        ``get_firing_edges`` deliberately hides a group edge from any member that has its own edge
        for the same pair, so a member-based lookup can miss an edge that genuinely fired.
        """
        driver = self._require_driver()
        params: dict[str, Any] = {
            "factor_id": factor_id.value,
            "group_id": group_id,
            "condition": condition.value if condition is not None else None,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_GROUP_EDGE_COUNTS_CYPHER, params)
                row = await result.single()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j group-edge count query failed: {exc}") from exc
        if row is None:
            return None
        return float(row["alpha"]), float(row["beta"]), float(row["weight"])

    async def update_edge_weight(
        self,
        factor_id: EventType,
        asset_id: AssetId | str,
        *,
        weight: float,
        condition: ConditionCode | None = None,
        target_is_group: bool = False,
    ) -> None:
        """Persist the outcome-adjusted ``weight`` for a CAUSES edge (used by Credibility).

        Only ``weight`` is written: ``alpha``/``beta`` are frozen because reliability cancels out of
        the decision's net/total ratio for a single firing edge, so counting outcomes there changed
        nothing observable. ``weight`` is what feeds magnitude.

        When ``condition`` is given the conditioned edge is updated; otherwise the legacy
        unconditional edge is updated (keeps older scored messages working).

        With ``target_is_group`` the edge targets an ``:AssetGroup`` rather than an ``:Asset`` —
        the inherited industry prior a prediction actually fired. ``asset_id`` then carries the
        group id as a plain string, because a group id is not a registry-validated ``AssetId``.
        """
        driver = self._require_driver()
        target_id = asset_id.value if isinstance(asset_id, AssetId) else str(asset_id)
        if condition is None:
            cypher = _UPDATE_GROUP_EDGE_CYPHER if target_is_group else _UPDATE_EDGE_CYPHER
            params: dict[str, Any] = {
                "factor_id": factor_id.value,
                "target_id": target_id,
                "weight": weight,
            }
            edge_label = f"{factor_id.value}->{target_id}"
        else:
            cypher = (
                _UPDATE_CONDITIONED_GROUP_EDGE_CYPHER
                if target_is_group
                else _UPDATE_CONDITIONED_EDGE_CYPHER
            )
            params = {
                "factor_id": factor_id.value,
                "target_id": target_id,
                "condition": condition.value,
                "weight": weight,
            }
            edge_label = f"{factor_id.value}|{condition.value}->{target_id}"
        try:
            async with driver.session() as session:
                result = await session.run(cypher, params)
                updated = await result.single()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j edge-weight update failed: {exc}") from exc
        if updated is None:
            kind = "AssetGroup" if target_is_group else "Asset"
            raise GraphTransportError(f"no CAUSES edge for {edge_label} (target {kind})")

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
    ) -> bool:
        """Create a conditioned edge if absent (used by the offline structure learner).

        Add-only: ``ON CREATE SET`` means an edge that already exists is left completely untouched.
        Creates the factor/asset nodes if absent.

        Returns **True only when a relationship was actually created**, so the caller can report how
        much new structure was discovered rather than how many upserts it attempted.
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
                result = await session.run(_UPSERT_CONDITIONED_EDGE_CYPHER, params)
                return await self._relationship_created(result)
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(f"neo4j conditioned-edge upsert failed: {exc}") from exc

    async def get_correlation_edges(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
    ) -> list[CorrelationEdge]:
        """Return CORRELATES_WITH edges from source_asset_id active under condition.

        Used by the Prediction Service propagation pass to find downstream assets that
        should receive a secondary prediction when source_asset_id is predicted in the
        direction implied by condition (UPSTREAM_UP or UPSTREAM_DOWN).
        """
        driver = self._require_driver()
        params = {
            "source_asset_id": source_asset_id.value,
            "condition": condition.value,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_CORRELATION_EDGES_CYPHER, params)
                records = await result.data()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(
                f"neo4j correlation-edge query failed: {exc}"
            ) from exc

        edges: list[CorrelationEdge] = []
        for row in cast(list[dict[str, Any]], records):
            edges.append(
                CorrelationEdge(
                    source_asset_id=AssetId(row["source_asset_id"]),
                    target_asset_id=AssetId(row["target_asset_id"]),
                    condition=ConditionCode(row["condition"]),
                    direction=Direction(row["direction"]),
                    weight=float(row["weight"]),
                    confidence=float(row["confidence"]),
                    alpha=float(row["alpha"]),
                    beta=float(row["beta"]),
                )
            )
        return edges

    async def get_correlation_edge_counts(
        self,
        source_asset_id: AssetId,
        target_asset_id: AssetId,
        condition: ConditionCode,
    ) -> tuple[float, float, float] | None:
        """Return ``(alpha, beta, weight)`` for one CORRELATES_WITH edge, or ``None`` when absent.

        Mirrors ``get_group_edge_counts``. ``weight`` is what Credibility adjusts from an outcome;
        ``alpha``/``beta`` are returned for reporting only, since they are no longer moved by
        outcomes.
        """
        driver = self._require_driver()
        params: dict[str, Any] = {
            "source_asset_id": source_asset_id.value,
            "target_asset_id": target_asset_id.value,
            "condition": condition.value,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_CORRELATION_EDGE_COUNTS_CYPHER, params)
                row = await result.single()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(
                f"neo4j correlation-edge count query failed: {exc}"
            ) from exc
        if row is None:
            return None
        return float(row["alpha"]), float(row["beta"]), float(row["weight"])

    async def update_correlation_weight(
        self,
        source_asset_id: AssetId,
        target_asset_id: AssetId,
        condition: ConditionCode,
        *,
        weight: float,
    ) -> None:
        """Persist the outcome-adjusted ``weight`` for a CORRELATES_WITH edge (used by Credibility).

        Only ``weight`` is written; ``alpha``/``beta`` are frozen. See ``update_edge_weight``.

        Raises GraphTransportError when the edge does not exist.
        """
        driver = self._require_driver()
        params: dict[str, Any] = {
            "source_asset_id": source_asset_id.value,
            "target_asset_id": target_asset_id.value,
            "condition": condition.value,
            "weight": weight,
        }
        edge_label = (
            f"{source_asset_id.value}|{condition.value}->{target_asset_id.value}"
        )
        try:
            async with driver.session() as session:
                result = await session.run(_UPDATE_CORRELATION_EDGE_CYPHER, params)
                updated = await result.single()
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(
                f"neo4j correlation-edge update failed: {exc}"
            ) from exc
        if updated is None:
            raise GraphTransportError(
                f"no CORRELATES_WITH edge for {edge_label}"
            )

    async def upsert_correlation_edge(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
        target_asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> bool:
        """Create a CORRELATES_WITH edge if absent (used by the offline structure learner).

        Add-only: ``ON CREATE SET`` means an edge that already exists is left completely untouched.
        Creates Asset nodes if absent.

        Returns **True only when a relationship was actually created** — see
        ``upsert_conditioned_edge``.
        """
        driver = self._require_driver()
        params = {
            "source_asset_id": source_asset_id.value,
            "target_asset_id": target_asset_id.value,
            "condition": condition.value,
            "direction": direction.value,
            "weight": weight,
            "confidence": confidence,
            "alpha": alpha,
            "beta": beta,
        }
        try:
            async with driver.session() as session:
                result = await session.run(_UPSERT_CORRELATION_EDGE_CYPHER, params)
                return await self._relationship_created(result)
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise GraphTransportError(
                f"neo4j correlation-edge upsert failed: {exc}"
            ) from exc

    @staticmethod
    async def _relationship_created(result: Any) -> bool:
        """True when the just-run statement created a relationship.

        Read from the driver's own write counters rather than from a marker property on the edge: a
        marker would require an ``ON MATCH SET``, which would mutate the very edges the add-only
        rule exists to protect.
        """
        summary = await result.consume()
        return bool(getattr(summary.counters, "relationships_created", 0))
