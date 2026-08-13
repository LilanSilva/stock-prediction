from __future__ import annotations

from typing import Any

import pytest

from shared.graph import CausalGraphClient, CorrelationEdge, FiringEdge, Neo4jSettings
from shared.graph.exceptions import GraphConfigurationError, GraphTransportError
from shared.schemas.messages import AssetId, ConditionCode, Direction, EventType


class _FakeCounters:
    def __init__(self, relationships_created: int) -> None:
        self.relationships_created = relationships_created


class _FakeSummary:
    def __init__(self, relationships_created: int) -> None:
        self.counters = _FakeCounters(relationships_created)


class _FakeResult:
    def __init__(
        self, rows: list[dict[str, Any]], *, relationships_created: int = 0
    ) -> None:
        self._rows = rows
        self._relationships_created = relationships_created

    async def data(self) -> list[dict[str, Any]]:
        return self._rows

    async def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def consume(self) -> _FakeSummary:
        """Mirrors the driver's write counters, which is how add-only upserts report creation."""
        return _FakeSummary(self._relationships_created)


class _FakeSession:
    """Query-aware stub: asset-level and group-level edge queries return different rows.

    ``get_firing_edges`` issues two Cypher statements (the asset's own edges, then any inherited
    from its group), so a stub that answered both identically would double every edge.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        group_rows: list[dict[str, Any]] | None = None,
        raise_exc: Exception | None = None,
        relationships_created: int = 0,
    ) -> None:
        self._rows = rows
        self._group_rows = group_rows or []
        self._raise = raise_exc
        self._relationships_created = relationships_created
        self.run_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def run(self, cypher: str, params: dict[str, Any] | None = None) -> _FakeResult:
        self.run_calls.append((cypher, params))
        if self._raise is not None:
            raise self._raise
        rows = self._group_rows if "AssetGroup" in cypher else self._rows
        return _FakeResult(rows, relationships_created=self._relationships_created)


class _FakeDriver:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        group_rows: list[dict[str, Any]] | None = None,
        raise_exc: Exception | None = None,
        relationships_created: int = 0,
    ) -> None:
        self._rows = rows
        self._group_rows = group_rows
        self._raise = raise_exc
        self._relationships_created = relationships_created
        self.last_session: _FakeSession | None = None

    def session(self) -> _FakeSession:
        self.last_session = _FakeSession(
            self._rows,
            group_rows=self._group_rows,
            raise_exc=self._raise,
            relationships_created=self._relationships_created,
        )
        return self.last_session

    async def close(self) -> None:
        return None

    async def verify_connectivity(self) -> None:
        return None


def _client_with(driver: _FakeDriver) -> CausalGraphClient:
    client = CausalGraphClient(Neo4jSettings())
    client._driver = driver
    return client


def test_firing_edge_derived_fields() -> None:
    edge = FiringEdge(
        factor_id=EventType.MILITARY_CONFLICT,
        asset_id=AssetId.NEM_NYSE,
        direction=Direction.UP,
        weight=0.75,
        confidence=0.8,
        alpha=3.0,
        beta=1.0,
    )
    assert edge.edge_id == "MILITARY_CONFLICT->NEM_NYSE"
    assert edge.reliability == 0.75


async def test_get_firing_edges_parses_rows() -> None:
    driver = _FakeDriver(
        [
            {
                "factor_id": "MILITARY_CONFLICT",
                "asset_id": "NEM_NYSE",
                "direction": "UP",
                "weight": 0.75,
                "confidence": 0.8,
                "alpha": 1.0,
                "beta": 1.0,
            }
        ]
    )
    client = _client_with(driver)
    edges = await client.get_firing_edges(EventType.MILITARY_CONFLICT, [AssetId.NEM_NYSE])
    assert len(edges) == 1
    assert edges[0].asset_id == AssetId.NEM_NYSE
    assert edges[0].direction == Direction.UP
    assert edges[0].weight == 0.75
    assert edges[0].edge_id == "MILITARY_CONFLICT->NEM_NYSE"
    # The query is parameterized with canonical string values, never provider symbols.
    assert driver.last_session is not None
    _, params = driver.last_session.run_calls[0]
    assert params == {
        "event_type": "MILITARY_CONFLICT",
        "asset_ids": ["NEM_NYSE"],
        "conditions": None,
    }


async def test_get_firing_edges_empty_returns_empty_list() -> None:
    client = _client_with(_FakeDriver([]))
    assert await client.get_firing_edges(EventType.CORPORATE_EARNINGS) == []


async def test_get_firing_edges_transport_error_is_normalized() -> None:
    client = _client_with(_FakeDriver([], raise_exc=RuntimeError("bolt down")))
    with pytest.raises(GraphTransportError, match="firing-edge query failed"):
        await client.get_firing_edges(EventType.SANCTIONS)


async def test_query_without_connect_raises_configuration_error() -> None:
    client = CausalGraphClient(Neo4jSettings())
    with pytest.raises(GraphConfigurationError, match="connect"):
        await client.get_firing_edges(EventType.SANCTIONS)


async def test_update_edge_weight_ok() -> None:
    driver = _FakeDriver([{"weight": 0.42}])
    client = _client_with(driver)
    await client.update_edge_weight(
        EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE, weight=0.42
    )
    assert driver.last_session is not None
    cypher, params = driver.last_session.run_calls[0]
    assert ":Asset {id: $target_id}" in cypher
    # Only weight is written: alpha/beta are frozen for KG edges.
    assert "r.weight = $weight" in cypher
    assert "r.alpha" not in cypher
    assert params == {
        "factor_id": "MILITARY_CONFLICT",
        "target_id": "NEM_NYSE",
        "weight": 0.42,
    }


async def test_update_edge_weight_targets_asset_group_when_flagged() -> None:
    # An inherited edge names the industry group, so learning must land on the group edge itself
    # rather than on a per-asset edge that was never seeded.
    driver = _FakeDriver([], group_rows=[{"weight": 0.30}])
    client = _client_with(driver)
    await client.update_edge_weight(
        EventType.MILITARY_CONFLICT,
        "WEAPON_INDUSTRY",
        weight=0.30,
        target_is_group=True,
    )
    assert driver.last_session is not None
    cypher, params = driver.last_session.run_calls[0]
    assert ":AssetGroup {id: $target_id}" in cypher
    assert params is not None
    assert params["target_id"] == "WEAPON_INDUSTRY"


async def test_update_conditioned_group_edge_uses_condition_and_group_label() -> None:
    driver = _FakeDriver([], group_rows=[{"weight": 0.55}])
    client = _client_with(driver)
    await client.update_edge_weight(
        EventType.MILITARY_CONFLICT,
        "WEAPON_INDUSTRY",
        weight=0.55,
        condition=ConditionCode.TRANSPORT_AFFECTED,
        target_is_group=True,
    )
    assert driver.last_session is not None
    cypher, params = driver.last_session.run_calls[0]
    assert ":AssetGroup {id: $target_id}" in cypher
    assert params is not None
    assert params["condition"] == "TRANSPORT_AFFECTED"
    assert params["target_id"] == "WEAPON_INDUSTRY"


async def test_update_group_edge_missing_raises_naming_group_target() -> None:
    client = _client_with(_FakeDriver([]))
    with pytest.raises(GraphTransportError, match="target AssetGroup"):
        await client.update_edge_weight(
            EventType.OTHER, "WEAPON_INDUSTRY", weight=0.5, target_is_group=True
        )


async def test_get_group_edge_counts_returns_counts_and_weight() -> None:
    driver = _FakeDriver([], group_rows=[{"alpha": 4.0, "beta": 3.0, "weight": 0.65}])
    client = _client_with(driver)
    counts = await client.get_group_edge_counts(
        EventType.MILITARY_CONFLICT, "WEAPON_INDUSTRY"
    )
    # weight is the quantity Credibility adjusts; alpha/beta come back for reporting only.
    assert counts == (4.0, 3.0, 0.65)
    assert driver.last_session is not None
    _, params = driver.last_session.run_calls[0]
    assert params == {
        "factor_id": "MILITARY_CONFLICT",
        "group_id": "WEAPON_INDUSTRY",
        "condition": None,
    }


async def test_get_group_edge_counts_returns_none_when_absent() -> None:
    client = _client_with(_FakeDriver([]))
    assert (
        await client.get_group_edge_counts(EventType.OTHER, "WEAPON_INDUSTRY") is None
    )


async def test_update_edge_weight_missing_edge_raises() -> None:
    client = _client_with(_FakeDriver([]))
    with pytest.raises(GraphTransportError, match="no CAUSES edge"):
        await client.update_edge_weight(EventType.OTHER, AssetId.NEM_NYSE, weight=0.5)


async def test_learner_upserts_are_add_only() -> None:
    # The offline learner may DISCOVER edges but never revise one that already exists: a bare SET
    # let a batch run overwrite an expert-seeded edge (and the online weight) from a few samples.
    from shared.graph.client import (
        _UPSERT_CONDITIONED_EDGE_CYPHER,
        _UPSERT_CORRELATION_EDGE_CYPHER,
    )

    for cypher in (_UPSERT_CONDITIONED_EDGE_CYPHER, _UPSERT_CORRELATION_EDGE_CYPHER):
        assert "ON CREATE SET" in cypher
        # No unguarded SET clause that would fire on an existing edge.
        assert "\nSET " not in cypher
        # No ON MATCH either: even a bookkeeping property would mutate a protected edge.
        assert "ON MATCH" not in cypher


async def test_upsert_reports_whether_it_created_the_edge() -> None:
    # The learner logs edges CREATED, not upserts attempted, so this flag has to be honest: an
    # estimate for an existing edge changes nothing and must come back False.
    created = _FakeDriver([{"alpha": 1.0, "beta": 1.0}], relationships_created=1)
    assert (
        await _client_with(created).upsert_correlation_edge(
            AssetId.XOM_NYSE,
            ConditionCode.UPSTREAM_UP,
            AssetId.NEM_NYSE,
            direction=Direction.DOWN,
            weight=0.45,
            confidence=0.6,
            alpha=1.0,
            beta=1.0,
        )
        is True
    )

    existing = _FakeDriver([{"alpha": 1.0, "beta": 1.0}], relationships_created=0)
    assert (
        await _client_with(existing).upsert_correlation_edge(
            AssetId.XOM_NYSE,
            ConditionCode.UPSTREAM_UP,
            AssetId.NEM_NYSE,
            direction=Direction.DOWN,
            weight=0.45,
            confidence=0.6,
            alpha=1.0,
            beta=1.0,
        )
        is False
    )


async def test_verify_connectivity_false_without_driver() -> None:
    client = CausalGraphClient(Neo4jSettings())
    assert client.is_connected is False
    assert await client.verify_connectivity() is False


# --- CorrelationEdge model tests ---


def test_correlation_edge_reliability() -> None:
    edge = CorrelationEdge(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        weight=0.45,
        confidence=0.8,
        alpha=3.0,
        beta=1.0,
    )
    assert edge.reliability == pytest.approx(0.75)


def test_correlation_edge_edge_id() -> None:
    edge = CorrelationEdge(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        weight=0.45,
        confidence=0.8,
        alpha=1.0,
        beta=1.0,
    )
    assert edge.edge_id == "XOM_NYSE|UPSTREAM_UP->NEM_NYSE"


# --- get_correlation_edges tests ---


async def test_get_correlation_edges_parses_row() -> None:
    driver = _FakeDriver(
        [
            {
                "source_asset_id": "XOM_NYSE",
                "target_asset_id": "NEM_NYSE",
                "condition": "UPSTREAM_UP",
                "direction": "DOWN",
                "weight": 0.45,
                "confidence": 0.8,
                "alpha": 2.0,
                "beta": 1.0,
            }
        ]
    )
    client = _client_with(driver)
    edges = await client.get_correlation_edges(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP)
    assert len(edges) == 1
    assert edges[0].source_asset_id is AssetId.XOM_NYSE
    assert edges[0].target_asset_id is AssetId.NEM_NYSE
    assert edges[0].direction is Direction.DOWN
    assert edges[0].weight == 0.45
    assert driver.last_session is not None
    _, params = driver.last_session.run_calls[0]
    assert params == {"source_asset_id": "XOM_NYSE", "condition": "UPSTREAM_UP"}


async def test_get_correlation_edges_empty_returns_empty_list() -> None:
    client = _client_with(_FakeDriver([]))
    result = await client.get_correlation_edges(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_DOWN)
    assert result == []


async def test_get_correlation_edges_transport_error_is_normalized() -> None:
    client = _client_with(_FakeDriver([], raise_exc=RuntimeError("bolt down")))
    with pytest.raises(GraphTransportError, match="correlation-edge query failed"):
        await client.get_correlation_edges(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP)


# --- get_correlation_edge_counts tests ---


async def test_get_correlation_edge_counts_returns_tuple_when_present() -> None:
    driver = _FakeDriver([{"alpha": 3.0, "beta": 2.0, "weight": 0.45}])
    client = _client_with(driver)
    counts = await client.get_correlation_edge_counts(
        AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP
    )
    assert counts == (3.0, 2.0, 0.45)
    assert driver.last_session is not None
    _, params = driver.last_session.run_calls[0]
    assert params == {
        "source_asset_id": "XOM_NYSE",
        "target_asset_id": "NEM_NYSE",
        "condition": "UPSTREAM_UP",
    }


async def test_get_correlation_edge_counts_returns_none_when_absent() -> None:
    client = _client_with(_FakeDriver([]))
    result = await client.get_correlation_edge_counts(
        AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP
    )
    assert result is None


# --- update_correlation_weight tests ---


async def test_update_correlation_weight_sends_correct_params() -> None:
    driver = _FakeDriver([{"weight": 0.38}])
    client = _client_with(driver)
    await client.update_correlation_weight(
        AssetId.XOM_NYSE,
        AssetId.NEM_NYSE,
        ConditionCode.UPSTREAM_UP,
        weight=0.38,
    )
    assert driver.last_session is not None
    cypher, params = driver.last_session.run_calls[0]
    assert "CORRELATES_WITH" in cypher
    assert "r.weight = $weight" in cypher
    assert "r.alpha" not in cypher  # reliability is frozen for KG edges
    assert params == {
        "source_asset_id": "XOM_NYSE",
        "target_asset_id": "NEM_NYSE",
        "condition": "UPSTREAM_UP",
        "weight": 0.38,
    }


async def test_update_correlation_weight_missing_edge_raises() -> None:
    client = _client_with(_FakeDriver([]))
    with pytest.raises(GraphTransportError, match="no CORRELATES_WITH edge"):
        await client.update_correlation_weight(
            AssetId.XOM_NYSE,
            AssetId.NEM_NYSE,
            ConditionCode.UPSTREAM_UP,
            weight=0.5,
        )


# --- upsert_correlation_edge tests ---


async def test_upsert_correlation_edge_sends_correct_params() -> None:
    driver = _FakeDriver([{"alpha": 3.0, "beta": 1.0}])
    client = _client_with(driver)
    await client.upsert_correlation_edge(
        AssetId.XOM_NYSE,
        ConditionCode.UPSTREAM_UP,
        AssetId.NEM_NYSE,
        direction=Direction.DOWN,
        weight=0.45,
        confidence=0.8,
        alpha=3.0,
        beta=1.0,
    )
    assert driver.last_session is not None
    cypher, params = driver.last_session.run_calls[0]
    assert "CORRELATES_WITH" in cypher
    assert params == {
        "source_asset_id": "XOM_NYSE",
        "target_asset_id": "NEM_NYSE",
        "condition": "UPSTREAM_UP",
        "direction": "DOWN",
        "weight": 0.45,
        "confidence": 0.8,
        "alpha": 3.0,
        "beta": 1.0,
    }


# --- industry-level edge inheritance (S4) --------------------------------------------------------


def _asset_row(asset_id: str = "SAAB_B_STO", **over: Any) -> dict[str, Any]:
    row = {
        "factor_id": "MILITARY_CONFLICT",
        "asset_id": asset_id,
        "direction": "UP",
        "weight": 0.75,
        "confidence": 0.8,
        "alpha": 1.0,
        "beta": 1.0,
    }
    row.update(over)
    return row


async def test_group_edges_are_inherited_by_member_assets() -> None:
    # A newly listed company has no edges of its own yet; it must still predict, using the industry
    # prior, rather than waiting for the offline learner to accumulate company-specific samples.
    driver = _FakeDriver([], group_rows=[_asset_row(group_id="WEAPON_INDUSTRY")])
    edges = await _client_with(driver).get_firing_edges(
        EventType.MILITARY_CONFLICT, [AssetId.SAAB_B_STO]
    )
    assert len(edges) == 1
    assert edges[0].asset_id == AssetId.SAAB_B_STO
    assert edges[0].inherited_from == "WEAPON_INDUSTRY"


async def test_inherited_edge_id_points_at_the_group_edge() -> None:
    # Learning must flow back to the industry prior that actually fired, not to a per-asset edge
    # that does not exist in the graph.
    driver = _FakeDriver([], group_rows=[_asset_row(group_id="WEAPON_INDUSTRY")])
    edges = await _client_with(driver).get_firing_edges(EventType.MILITARY_CONFLICT)
    assert edges[0].edge_id == "MILITARY_CONFLICT->WEAPON_INDUSTRY"


async def test_inherited_conditioned_edge_id_includes_the_condition() -> None:
    driver = _FakeDriver(
        [],
        group_rows=[_asset_row(group_id="WEAPON_INDUSTRY", condition="SAFE_HAVEN_ONLY")],
    )
    edges = await _client_with(driver).get_firing_edges(EventType.MILITARY_CONFLICT)
    assert edges[0].edge_id == "MILITARY_CONFLICT|SAFE_HAVEN_ONLY->WEAPON_INDUSTRY"


async def test_own_edges_and_group_edges_both_surface() -> None:
    # The Cypher excludes a group edge when the asset has its own for that (factor, condition) pair,
    # so what arrives here is already de-duplicated; the client must not drop either kind.
    driver = _FakeDriver(
        [_asset_row("NEM_NYSE")],
        group_rows=[_asset_row("SAAB_B_STO", group_id="WEAPON_INDUSTRY")],
    )
    edges = await _client_with(driver).get_firing_edges(EventType.MILITARY_CONFLICT)
    by_asset = {e.asset_id: e for e in edges}
    assert by_asset[AssetId.NEM_NYSE].inherited_from is None
    assert by_asset[AssetId.SAAB_B_STO].inherited_from == "WEAPON_INDUSTRY"


async def test_inheritance_can_be_disabled() -> None:
    driver = _FakeDriver([_asset_row("NEM_NYSE")], group_rows=[_asset_row(group_id="WEAPON_INDUSTRY")])
    edges = await _client_with(driver).get_firing_edges(
        EventType.MILITARY_CONFLICT, inherit_group_edges=False
    )
    assert [e.asset_id for e in edges] == [AssetId.NEM_NYSE]
    # Only the asset-level query ran.
    assert driver.last_session is not None
    assert not any("AssetGroup" in c for c, _ in driver.last_session.run_calls)


async def test_own_edge_wins_over_group_edge_for_the_same_pair() -> None:
    # Asserted at the query level: the group query filters out any (factor, condition) the asset
    # already has, so a company-specific edge overrides the industry prior instead of adding to it.
    driver = _FakeDriver([_asset_row("SAAB_B_STO")], group_rows=[])
    edges = await _client_with(driver).get_firing_edges(
        EventType.MILITARY_CONFLICT, [AssetId.SAAB_B_STO]
    )
    assert len(edges) == 1
    assert edges[0].inherited_from is None
    assert edges[0].edge_id == "MILITARY_CONFLICT->SAAB_B_STO"
    calls = driver.last_session.run_calls if driver.last_session else []
    group_cypher = [c for c, _ in calls if "AssetGroup" in c]
    assert group_cypher and "NOT EXISTS" in group_cypher[0]
