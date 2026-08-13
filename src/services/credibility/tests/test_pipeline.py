"""Unit tests for the credibility pipeline with fake graph + repository (no live infra)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from shared.graph.models import FiringEdge
from shared.reference import REGISTRY_VERSION
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    ConditionCode,
    ContributingEdge,
    Direction,
    EventType,
    Magnitude,
    PredictionScored,
    PriceKind,
    PropagationHop,
)

from credibility.exceptions import InvalidScoredMessageError
from credibility.pipeline import CredibilityPipeline, parse_edge_id
from credibility.updater import WeightUpdate


class FakeGraph:
    """In-memory stand-in for the shared CausalGraphClient.

    Edges are keyed by ``(factor, target, condition)`` so conditioned and unconditional edges that
    share a ``(factor, target)`` pair stay distinct (``condition=None`` is the unconditional edge).
    ``target`` is an ``AssetId`` for a per-asset edge, or a plain ``str`` group id for an
    industry-level (inherited) edge.

    Edge state is stored as ``(alpha, beta, weight)``. A test may supply a 2-tuple ``(alpha, beta)``
    when it does not care about the weight, and ``_DEFAULT_WEIGHT`` is filled in — most tests
    predate weight becoming the learned quantity and only assert on which edge was written.
    """

    _DEFAULT_WEIGHT = 0.5

    def __init__(
        self,
        edges: dict[tuple[EventType, AssetId, ConditionCode | None], tuple[float, ...]],
        group_edges: dict[tuple[EventType, str, ConditionCode | None], tuple[float, ...]]
        | None = None,
        corr_edges: dict[tuple[AssetId, AssetId, ConditionCode], tuple[float, ...]]
        | None = None,
    ) -> None:
        self._edges = {key: self._state(value) for key, value in edges.items()}
        self._group_edges = {
            key: self._state(value) for key, value in (group_edges or {}).items()
        }
        self._corr_edges = {
            key: self._state(value) for key, value in (corr_edges or {}).items()
        }
        self.writes: list[tuple[EventType, AssetId | str, ConditionCode | None, float]] = []
        self.group_writes: list[tuple[EventType, str, ConditionCode | None, float]] = []
        self.corr_writes: list[tuple[AssetId, AssetId, ConditionCode, float]] = []

    @classmethod
    def _state(cls, value: tuple[float, ...]) -> tuple[float, float, float]:
        alpha, beta = value[0], value[1]
        weight = value[2] if len(value) > 2 else cls._DEFAULT_WEIGHT
        return alpha, beta, weight

    async def get_group_edge_counts(
        self,
        factor_id: EventType,
        group_id: str,
        condition: ConditionCode | None = None,
    ) -> tuple[float, float, float] | None:
        return self._group_edges.get((factor_id, group_id, condition))

    async def get_firing_edges(
        self, event_type: EventType, asset_ids: list[AssetId] | None = None
    ) -> list[FiringEdge]:
        out: list[FiringEdge] = []
        for (factor, asset, condition), (alpha, beta, weight) in self._edges.items():
            if factor != event_type:
                continue
            if asset_ids is not None and asset not in asset_ids:
                continue
            out.append(
                FiringEdge(
                    factor_id=factor,
                    asset_id=asset,
                    direction=Direction.UP,
                    weight=weight,
                    confidence=0.5,
                    alpha=alpha,
                    beta=beta,
                    condition=condition,
                )
            )
        return out

    async def update_edge_weight(
        self,
        factor_id: EventType,
        asset_id: AssetId | str,
        *,
        weight: float,
        condition: ConditionCode | None = None,
        target_is_group: bool = False,
    ) -> None:
        self.writes.append((factor_id, asset_id, condition, weight))
        if target_is_group:
            key = (factor_id, str(asset_id), condition)
            self.group_writes.append((factor_id, str(asset_id), condition, weight))
            alpha, beta, _ = self._group_edges.get(key, (1.0, 1.0, weight))
            self._group_edges[key] = (alpha, beta, weight)
        else:
            assert isinstance(asset_id, AssetId)
            asset_key = (factor_id, asset_id, condition)
            alpha, beta, _ = self._edges.get(asset_key, (1.0, 1.0, weight))
            self._edges[asset_key] = (alpha, beta, weight)

    async def get_correlation_edge_counts(
        self,
        source_asset_id: AssetId,
        target_asset_id: AssetId,
        condition: ConditionCode,
    ) -> tuple[float, float, float] | None:
        return self._corr_edges.get((source_asset_id, target_asset_id, condition))

    async def update_correlation_weight(
        self,
        source_asset_id: AssetId,
        target_asset_id: AssetId,
        condition: ConditionCode,
        *,
        weight: float,
    ) -> None:
        key = (source_asset_id, target_asset_id, condition)
        self.corr_writes.append((source_asset_id, target_asset_id, condition, weight))
        alpha, beta, _ = self._corr_edges.get(key, (1.0, 1.0, weight))
        self._corr_edges[key] = (alpha, beta, weight)


class FakeRepo:
    """In-memory stand-in for CredibilityRepository."""

    def __init__(
        self,
        *,
        processed: set[uuid.UUID] | None = None,
        sources: dict[str, tuple[float, float]] | None = None,
        status: str = "PENDING",
    ) -> None:
        self._processed = processed or set()
        self._sources = sources or {}
        self._status = status
        self.committed: list[tuple[uuid.UUID, list[WeightUpdate]]] = []

    async def already_processed(self, prediction_id: uuid.UUID) -> bool:
        return prediction_id in self._processed

    async def prediction_status(self, prediction_id: uuid.UUID) -> str | None:
        return self._status

    async def get_source_state(self, source_id: str) -> tuple[float, float] | None:
        return self._sources.get(source_id)

    async def commit_updates(
        self, prediction_id: uuid.UUID, updates: list[WeightUpdate]
    ) -> bool:
        if prediction_id in self._processed:
            return False
        self._processed.add(prediction_id)
        self.committed.append((prediction_id, updates))
        return True


def _close(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        fetched_at=datetime.now(UTC),
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version=REGISTRY_VERSION,
    )


def _scored(
    *,
    is_correct: bool,
    edges: list[tuple[str, float]],
    sources: list[str],
    prediction_id: uuid.UUID | None = None,
) -> PredictionScored:
    return PredictionScored(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        prediction_id=prediction_id or uuid.uuid4(),
        context_id=uuid.uuid4(),
        asset_id=AssetId.NEM_NYSE,
        predicted_direction=Direction.UP,
        actual_direction=Direction.UP if is_correct else Direction.DOWN,
        predicted_magnitude=Magnitude.MEDIUM,
        actual_magnitude=Magnitude.MEDIUM,
        confidence=0.8,
        actual_return=0.02 if is_correct else -0.02,
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
        contributing_edges=[
            ContributingEdge(
                edge_id=edge_id,
                direction=Direction.UP,
                current_weight=0.5,
                influence_weight=influence,
                path=edge_id,
            )
            for edge_id, influence in edges
        ],
        source_ids=sources,
        baseline=_close(date(2026, 7, 27), "100.00"),
        settlement=_close(date(2026, 7, 28), "102.00"),
        scored_at=datetime.now(UTC),
    )


def test_parse_edge_id_unconditional() -> None:
    assert parse_edge_id("MILITARY_CONFLICT->NEM_NYSE") == (
        EventType.MILITARY_CONFLICT,
        None,
        AssetId.NEM_NYSE,
    )


def test_parse_edge_id_conditioned() -> None:
    assert parse_edge_id("MILITARY_CONFLICT|TRANSPORT_AFFECTED->XOM_NYSE") == (
        EventType.MILITARY_CONFLICT,
        ConditionCode.TRANSPORT_AFFECTED,
        AssetId.XOM_NYSE,
    )


def test_parse_edge_id_malformed() -> None:
    with pytest.raises(InvalidScoredMessageError):
        parse_edge_id("no-arrow")


def test_parse_edge_id_unknown_factor() -> None:
    with pytest.raises(InvalidScoredMessageError):
        parse_edge_id("NOT_A_FACTOR->NEM_NYSE")


def test_parse_edge_id_unknown_condition() -> None:
    with pytest.raises(InvalidScoredMessageError):
        parse_edge_id("MILITARY_CONFLICT|NOT_A_CONDITION->XOM_NYSE")


def test_parse_edge_id_accepts_industry_group_target() -> None:
    # Regression: an inherited edge names the GROUP, not an asset. Coercing the target to AssetId
    # dead-lettered every scored prediction that fired a group edge, silently losing the learning.
    factor, condition, target = parse_edge_id("MILITARY_CONFLICT->WEAPON_INDUSTRY")
    assert factor is EventType.MILITARY_CONFLICT
    assert condition is None
    assert target == "WEAPON_INDUSTRY"
    assert not isinstance(target, AssetId)


def test_parse_edge_id_accepts_conditioned_industry_group_target() -> None:
    factor, condition, target = parse_edge_id(
        "MILITARY_CONFLICT|TRANSPORT_AFFECTED->WEAPON_INDUSTRY"
    )
    assert factor is EventType.MILITARY_CONFLICT
    assert condition is ConditionCode.TRANSPORT_AFFECTED
    assert target == "WEAPON_INDUSTRY"


def test_parse_edge_id_rejects_target_that_is_neither_asset_nor_group() -> None:
    with pytest.raises(InvalidScoredMessageError, match="neither a known asset"):
        parse_edge_id("MILITARY_CONFLICT->NOT_A_THING")


async def test_inherited_group_edge_credit_lands_on_the_group_prior() -> None:
    # The industry prior that actually fired must receive the credit — not a per-asset edge
    # that was never seeded, and not the asset's own unrelated edge.
    graph = FakeGraph(
        {(EventType.MILITARY_CONFLICT, AssetId("LMT_NYSE"), None): (9.0, 9.0)},
        group_edges={(EventType.MILITARY_CONFLICT, "WEAPON_INDUSTRY", None): (1.0, 1.0)},
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    # source_ids is empty, exactly as the real dead-lettered messages were (Cleansing does not
    # populate it yet), so this also proves an edge-only update still commits.
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->WEAPON_INDUSTRY", 1.0)],
        sources=[],
    )

    assert await pipeline.process(message) is True

    assert len(graph.group_writes) == 1
    factor, group_id, condition, weight = graph.group_writes[0]
    assert (factor, group_id, condition) == (
        EventType.MILITARY_CONFLICT,
        "WEAPON_INDUSTRY",
        None,
    )
    assert weight > FakeGraph._DEFAULT_WEIGHT, (
        "a correct prediction must raise the group edge's weight"
    )
    # The asset's own edge is untouched: credit follows the edge that fired.
    assert graph._edges[(EventType.MILITARY_CONFLICT, AssetId("LMT_NYSE"), None)] == (
        9.0,
        9.0,
        FakeGraph._DEFAULT_WEIGHT,
    )


async def test_missing_group_edge_is_skipped_not_dead_lettered() -> None:
    graph = FakeGraph({}, group_edges={})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->WEAPON_INDUSTRY", 1.0)],
        sources=[],
    )

    assert await pipeline.process(message) is True
    assert graph.group_writes == []


async def test_hit_raises_edge_weight_proportionally() -> None:
    graph = FakeGraph(
        {
            (EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE, None): (1.0, 1.0, 0.50),
            (EventType.INFLATION_CHANGE, AssetId.NEM_NYSE, None): (1.0, 1.0, 0.50),
        }
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0, weight_step=0.10)
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->NEM_NYSE", 0.7), ("INFLATION_CHANGE->NEM_NYSE", 0.3)],
        sources=[],
    )
    applied = await pipeline.process(message)
    assert applied is True

    written = {(f, a): weight for f, a, _c, weight in graph.writes}
    # step 0.10 scaled by each edge's credit share of the decision.
    assert written[(EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE)] == pytest.approx(0.57)
    assert written[(EventType.INFLATION_CHANGE, AssetId.NEM_NYSE)] == pytest.approx(0.53)


async def test_miss_lowers_edge_weight() -> None:
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.XOM_NYSE, None): (2.0, 2.0, 0.40)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0, weight_step=0.10)
    message = _scored(is_correct=False, edges=[("SANCTIONS->XOM_NYSE", 0.5)], sources=[])
    await pipeline.process(message)

    _factor, _asset, _condition, weight = graph.writes[0]
    assert weight == pytest.approx(0.30)  # sole edge -> full credit 1.0, so the whole step


async def test_outcome_leaves_edge_reliability_frozen() -> None:
    # alpha/beta are no longer the learned quantity: reliability cancels out of the decision's
    # net/total ratio for a single firing edge, so only weight is moved.
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.XOM_NYSE, None): (2.0, 2.0, 0.40)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    await pipeline.process(
        _scored(is_correct=False, edges=[("SANCTIONS->XOM_NYSE", 0.5)], sources=[])
    )

    _prediction_id, updates = repo.committed[0]
    edge_update = next(u for u in updates if u.entity_type == "edge")
    assert (edge_update.alpha_before, edge_update.alpha_after) == (2.0, 2.0)
    assert (edge_update.beta_before, edge_update.beta_after) == (2.0, 2.0)
    assert edge_update.weight_before is not None
    assert edge_update.weight_after is not None
    assert edge_update.weight_before == pytest.approx(0.40)
    assert edge_update.weight_after < edge_update.weight_before


async def test_withdrawn_prediction_is_ignored_but_recorded() -> None:
    # A superseded prediction never stood, so its outcome says nothing about the edges that made it.
    # Verification still scores it; deciding what to learn from belongs to Credibility.
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.XOM_NYSE, None): (2.0, 2.0, 0.40)})
    repo = FakeRepo(status="WITHDRAWN")
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)

    applied = await pipeline.process(
        _scored(is_correct=True, edges=[("SANCTIONS->XOM_NYSE", 1.0)], sources=[])
    )

    assert applied is False
    assert graph.writes == []          # no weight moved
    assert repo.committed == [(repo.committed[0][0], [])]  # guard claimed, nothing applied


async def test_missing_edge_is_skipped_not_fatal() -> None:
    graph = FakeGraph({(EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE, None): (1.0, 1.0)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->NEM_NYSE", 0.5), ("RECESSION_SIGNAL->NEM_NYSE", 0.5)],
        sources=[],
    )
    applied = await pipeline.process(message)
    assert applied is True
    # Only the present edge is written; the missing one is skipped.
    assert len(graph.writes) == 1
    _, prediction_updates = repo.committed[0]
    edge_ids = {u.entity_id for u in prediction_updates if u.entity_type == "edge"}
    assert edge_ids == {"MILITARY_CONFLICT->NEM_NYSE"}


async def test_sources_get_equal_credit_and_new_source_starts_at_prior() -> None:
    graph = FakeGraph({})
    repo = FakeRepo(sources={"di.se": (3.0, 1.0)})  # returning source
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(is_correct=True, edges=[], sources=["di.se", "svd.se"])
    await pipeline.process(message)

    _, updates = repo.committed[0]
    by_id = {u.entity_id: u for u in updates}
    # di.se was at (3,1) -> +0.5 alpha
    assert (by_id["di.se"].alpha_after, by_id["di.se"].beta_after) == pytest.approx((3.5, 1.0))
    # svd.se is new -> starts from prior (1,1) then +0.5 alpha
    assert (by_id["svd.se"].alpha_after, by_id["svd.se"].beta_after) == pytest.approx((1.5, 1.0))


async def test_duplicate_prediction_is_skipped() -> None:
    pid = uuid.uuid4()
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.NEM_NYSE, None): (1.0, 1.0)})
    repo = FakeRepo(processed={pid})
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True, edges=[("SANCTIONS->NEM_NYSE", 0.5)], sources=["di.se"], prediction_id=pid
    )
    applied = await pipeline.process(message)
    assert applied is False
    assert graph.writes == []  # no edge writes on a duplicate
    assert repo.committed == []


async def test_empty_sources_still_processes() -> None:
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.NEM_NYSE, None): (1.0, 1.0)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(is_correct=True, edges=[("SANCTIONS->NEM_NYSE", 1.0)], sources=[])
    applied = await pipeline.process(message)
    assert applied is True
    _, updates = repo.committed[0]
    assert all(u.entity_type == "edge" for u in updates)


async def test_conditioned_edge_passes_condition_to_graph_and_keys_history_by_full_id() -> None:
    # The conditioned edge must be selected and updated distinctly from the sibling unconditional
    # edge that shares the same (factor, asset) pair; only the conditioned one carries the message.
    graph = FakeGraph(
        {
            (EventType.MILITARY_CONFLICT, AssetId.XOM_NYSE, None): (1.0, 1.0),
            (EventType.MILITARY_CONFLICT, AssetId.XOM_NYSE, ConditionCode.TRANSPORT_AFFECTED): (
                1.0,
                1.0,
            ),
        }
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    edge_id = "MILITARY_CONFLICT|TRANSPORT_AFFECTED->XOM_NYSE"
    message = _scored(is_correct=True, edges=[(edge_id, 1.0)], sources=[])
    applied = await pipeline.process(message)
    assert applied is True

    assert len(graph.writes) == 1
    factor, asset, condition, weight = graph.writes[0]
    assert (factor, asset, condition) == (
        EventType.MILITARY_CONFLICT,
        AssetId.XOM_NYSE,
        ConditionCode.TRANSPORT_AFFECTED,
    )
    assert weight > FakeGraph._DEFAULT_WEIGHT  # sole edge -> full hit credit raises the weight

    _, updates = repo.committed[0]
    edge_ids = {u.entity_id for u in updates if u.entity_type == "edge"}
    assert edge_ids == {edge_id}  # history keyed by the full conditioned edge id, not collapsed


# --- E10 propagation branch ---


def _scored_propagated(
    *,
    is_correct: bool,
    source: AssetId = AssetId.XOM_NYSE,
    target: AssetId = AssetId.NEM_NYSE,
    condition: ConditionCode = ConditionCode.UPSTREAM_UP,
) -> PredictionScored:
    hop = PropagationHop(
        source_asset_id=source,
        target_asset_id=target,
        condition=condition,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    # Prediction reports the correlation edge in contributing_edges (that is what decide() saw),
    # and Credibility routes credit from there; propagation_chain marks it as propagated.
    edge_id = f"{source.value}|{condition.value}->{target.value}"
    return PredictionScored(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        asset_id=target,
        predicted_direction=Direction.DOWN,
        actual_direction=Direction.DOWN if is_correct else Direction.UP,
        predicted_magnitude=Magnitude.MEDIUM,
        actual_magnitude=Magnitude.MEDIUM,
        confidence=0.7,
        actual_return=-0.02 if is_correct else 0.02,
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
        contributing_edges=[
            ContributingEdge(
                edge_id=edge_id,
                direction=Direction.DOWN,
                current_weight=0.5,
                influence_weight=1.0,
                path=edge_id,
            )
        ],
        source_ids=[],
        baseline=_close(date(2026, 7, 27), "100.00"),
        settlement=_close(date(2026, 7, 28), "98.00"),
        scored_at=datetime.now(UTC),
        propagation_chain=[hop],
    )


async def test_correct_propagated_prediction_increments_alpha() -> None:
    graph = FakeGraph(
        {},
        corr_edges={(AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP): (2.0, 1.0)},
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    msg = _scored_propagated(is_correct=True)

    assert await pipeline.process(msg) is True
    assert len(graph.corr_writes) == 1
    src, tgt, cond, weight = graph.corr_writes[0]
    assert (src, tgt, cond) == (AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP)
    assert weight > FakeGraph._DEFAULT_WEIGHT, "correct prediction must raise the weight"


async def test_wrong_propagated_prediction_increments_beta() -> None:
    graph = FakeGraph(
        {},
        corr_edges={(AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP): (2.0, 1.0)},
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    msg = _scored_propagated(is_correct=False)

    assert await pipeline.process(msg) is True
    assert len(graph.corr_writes) == 1
    _, _, _, weight = graph.corr_writes[0]
    assert weight < FakeGraph._DEFAULT_WEIGHT, "wrong prediction must lower the weight"


async def test_direct_prediction_does_not_call_update_correlation_weight() -> None:
    # A PredictionScored with empty propagation_chain must NOT touch the correlation graph.
    graph = FakeGraph(
        {(EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE, None): (1.0, 1.0)},
        corr_edges={(AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP): (2.0, 1.0)},
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    msg = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->NEM_NYSE", 1.0)],
        sources=[],
    )

    await pipeline.process(msg)
    assert graph.corr_writes == [], "direct predictions must not touch CORRELATES_WITH edges"


async def test_propagated_prediction_missing_corr_edge_returns_no_update() -> None:
    # If the CORRELATES_WITH edge is absent from the graph, pipeline still completes (no crash),
    # just no edge update written.
    graph = FakeGraph({}, corr_edges={})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    msg = _scored_propagated(is_correct=True)

    applied = await pipeline.process(msg)
    assert applied is True
    assert graph.corr_writes == []


def _scored_converged(*, is_correct: bool) -> PredictionScored:
    """A propagated prediction produced by TWO converging correlation edges."""
    target = AssetId.SWED_A_STO
    a = f"NEM_NYSE|{ConditionCode.UPSTREAM_DOWN.value}->{target.value}"
    b = f"LUG_STO|{ConditionCode.UPSTREAM_DOWN.value}->{target.value}"
    hops = [
        PropagationHop(
            source_asset_id=AssetId.NEM_NYSE,
            target_asset_id=target,
            condition=ConditionCode.UPSTREAM_DOWN,
            direction=Direction.UP,
            edge_weight=0.90,
        ),
        PropagationHop(
            source_asset_id=AssetId.LUG_STO,
            target_asset_id=target,
            condition=ConditionCode.UPSTREAM_DOWN,
            direction=Direction.UP,
            edge_weight=0.20,
        ),
    ]
    return PredictionScored(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        asset_id=target,
        predicted_direction=Direction.UP,
        actual_direction=Direction.UP if is_correct else Direction.DOWN,
        predicted_magnitude=Magnitude.MEDIUM,
        actual_magnitude=Magnitude.MEDIUM,
        confidence=0.6,
        actual_return=0.02 if is_correct else -0.02,
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
        contributing_edges=[
            ContributingEdge(
                edge_id=a,
                direction=Direction.UP,
                current_weight=0.5,
                influence_weight=0.90,
                path=a,
            ),
            ContributingEdge(
                edge_id=b,
                direction=Direction.DOWN,
                current_weight=0.5,
                influence_weight=0.20,
                path=b,
            ),
        ],
        source_ids=[],
        baseline=_close(date(2026, 7, 27), "100.00"),
        settlement=_close(date(2026, 7, 28), "102.00"),
        scored_at=datetime.now(UTC),
        propagation_chain=hops,
    )


async def test_converging_edges_receive_proportional_credit() -> None:
    # Both converging correlation edges are credited, split by influence_weight -- neither is
    # treated as though it acted alone, and the heavier edge receives more evidence.
    graph = FakeGraph(
        {},
        corr_edges={
            (AssetId.NEM_NYSE, AssetId.SWED_A_STO, ConditionCode.UPSTREAM_DOWN): (1.0, 1.0),
            (AssetId.LUG_STO, AssetId.SWED_A_STO, ConditionCode.UPSTREAM_DOWN): (1.0, 1.0),
        },
    )
    repo = FakeRepo()
    base = FakeGraph._DEFAULT_WEIGHT
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0, weight_step=0.10)

    assert await pipeline.process(_scored_converged(is_correct=True)) is True

    assert len(graph.corr_writes) == 2, "both converging edges must be credited"
    by_source = {src: weight for src, _tgt, _c, weight in graph.corr_writes}
    nem_weight = by_source[AssetId.NEM_NYSE]
    lug_weight = by_source[AssetId.LUG_STO]
    # Correct prediction -> weight rises on both.
    assert nem_weight > base and lug_weight > base
    # Credit is proportional: 0.90 vs 0.20 influence.
    assert nem_weight > lug_weight
    # The two moves sum to one full step, i.e. a single observation shared between them.
    assert (nem_weight - base) + (lug_weight - base) == pytest.approx(0.10)


async def test_converging_edges_share_the_blame_when_wrong() -> None:
    graph = FakeGraph(
        {},
        corr_edges={
            (AssetId.NEM_NYSE, AssetId.SWED_A_STO, ConditionCode.UPSTREAM_DOWN): (1.0, 1.0),
            (AssetId.LUG_STO, AssetId.SWED_A_STO, ConditionCode.UPSTREAM_DOWN): (1.0, 1.0),
        },
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)

    assert await pipeline.process(_scored_converged(is_correct=False)) is True

    assert len(graph.corr_writes) == 2
    by_source = {src: weight for src, _tgt, _c, weight in graph.corr_writes}
    for source in (AssetId.NEM_NYSE, AssetId.LUG_STO):
        assert by_source[source] < FakeGraph._DEFAULT_WEIGHT, (
            f"{source} weight must fall on a miss"
        )
    # The heavier edge carries more of the blame, so it falls further.
    assert by_source[AssetId.NEM_NYSE] < by_source[AssetId.LUG_STO]


async def test_single_edge_propagation_still_gets_full_credit() -> None:
    # Regression: the common one-edge case must be unchanged by the proportional split.
    graph = FakeGraph(
        {},
        corr_edges={(AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP): (1.0, 1.0)},
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0, weight_step=0.10)
    assert await pipeline.process(_scored_propagated(is_correct=True)) is True
    _s, _t, _c, weight = graph.corr_writes[0]
    # Sole edge -> full credit, so the whole step is applied.
    assert weight == pytest.approx(FakeGraph._DEFAULT_WEIGHT + 0.10)


def test_parse_correlation_edge_id_recognises_correlation_form() -> None:
    from credibility.pipeline import parse_correlation_edge_id

    assert parse_correlation_edge_id("XOM_NYSE|UPSTREAM_UP->NEM_NYSE") == (
        AssetId.XOM_NYSE,
        ConditionCode.UPSTREAM_UP,
        AssetId.NEM_NYSE,
    )


def test_parse_correlation_edge_id_returns_none_for_causes_edges() -> None:
    # A CAUSES edge id must fall through so parse_edge_id handles it.
    from credibility.pipeline import parse_correlation_edge_id

    assert parse_correlation_edge_id("MILITARY_CONFLICT->NEM_NYSE") is None
    assert parse_correlation_edge_id("MILITARY_CONFLICT|TRANSPORT_AFFECTED->XOM_NYSE") is None
