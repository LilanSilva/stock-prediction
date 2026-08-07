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
    """

    def __init__(
        self,
        edges: dict[tuple[EventType, AssetId, ConditionCode | None], tuple[float, float]],
        group_edges: dict[tuple[EventType, str, ConditionCode | None], tuple[float, float]]
        | None = None,
    ) -> None:
        self._edges = edges
        self._group_edges = group_edges or {}
        self.writes: list[
            tuple[EventType, AssetId | str, ConditionCode | None, float, float]
        ] = []
        self.group_writes: list[
            tuple[EventType, str, ConditionCode | None, float, float]
        ] = []

    async def get_group_edge_counts(
        self,
        factor_id: EventType,
        group_id: str,
        condition: ConditionCode | None = None,
    ) -> tuple[float, float] | None:
        return self._group_edges.get((factor_id, group_id, condition))

    async def get_firing_edges(
        self, event_type: EventType, asset_ids: list[AssetId] | None = None
    ) -> list[FiringEdge]:
        out: list[FiringEdge] = []
        for (factor, asset, condition), (alpha, beta) in self._edges.items():
            if factor != event_type:
                continue
            if asset_ids is not None and asset not in asset_ids:
                continue
            out.append(
                FiringEdge(
                    factor_id=factor,
                    asset_id=asset,
                    direction=Direction.UP,
                    weight=0.5,
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
        alpha: float,
        beta: float,
        condition: ConditionCode | None = None,
        target_is_group: bool = False,
    ) -> None:
        self.writes.append((factor_id, asset_id, condition, alpha, beta))
        if target_is_group:
            self.group_writes.append((factor_id, str(asset_id), condition, alpha, beta))
            self._group_edges[(factor_id, str(asset_id), condition)] = (alpha, beta)
        else:
            assert isinstance(asset_id, AssetId)
            self._edges[(factor_id, asset_id, condition)] = (alpha, beta)


class FakeRepo:
    """In-memory stand-in for CredibilityRepository."""

    def __init__(
        self,
        *,
        processed: set[uuid.UUID] | None = None,
        sources: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._processed = processed or set()
        self._sources = sources or {}
        self.committed: list[tuple[uuid.UUID, list[WeightUpdate]]] = []

    async def already_processed(self, prediction_id: uuid.UUID) -> bool:
        return prediction_id in self._processed

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
    factor, group_id, condition, alpha, beta = graph.group_writes[0]
    assert (factor, group_id, condition) == (
        EventType.MILITARY_CONFLICT,
        "WEAPON_INDUSTRY",
        None,
    )
    assert alpha > 1.0, "a correct prediction must add credit to the group edge's alpha"
    assert beta == 1.0
    # The asset's own edge is untouched: credit follows the edge that fired.
    assert graph._edges[(EventType.MILITARY_CONFLICT, AssetId("LMT_NYSE"), None)] == (9.0, 9.0)


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


async def test_hit_adds_proportional_credit_to_edge_alpha() -> None:
    graph = FakeGraph(
        {
            (EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE, None): (1.0, 1.0),
            (EventType.INFLATION_CHANGE, AssetId.NEM_NYSE, None): (1.0, 1.0),
        }
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->NEM_NYSE", 0.7), ("INFLATION_CHANGE->NEM_NYSE", 0.3)],
        sources=[],
    )
    applied = await pipeline.process(message)
    assert applied is True

    written = {(f, a): (alpha, beta) for f, a, _c, alpha, beta in graph.writes}
    assert written[(EventType.MILITARY_CONFLICT, AssetId.NEM_NYSE)] == pytest.approx((1.7, 1.0))
    assert written[(EventType.INFLATION_CHANGE, AssetId.NEM_NYSE)] == pytest.approx((1.3, 1.0))


async def test_miss_adds_proportional_credit_to_edge_beta() -> None:
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.XOM_NYSE, None): (2.0, 2.0)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(is_correct=False, edges=[("SANCTIONS->XOM_NYSE", 0.5)], sources=[])
    await pipeline.process(message)

    _factor, _asset, _condition, alpha, beta = graph.writes[0]
    assert (alpha, beta) == pytest.approx((2.0, 3.0))  # sole edge -> full credit 1.0 to beta


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
    factor, asset, condition, alpha, beta = graph.writes[0]
    assert (factor, asset, condition) == (
        EventType.MILITARY_CONFLICT,
        AssetId.XOM_NYSE,
        ConditionCode.TRANSPORT_AFFECTED,
    )
    assert (alpha, beta) == pytest.approx((2.0, 1.0))  # sole edge -> full hit credit to alpha

    _, updates = repo.committed[0]
    edge_ids = {u.entity_id for u in updates if u.entity_type == "edge"}
    assert edge_ids == {edge_id}  # history keyed by the full conditioned edge id, not collapsed
